# API 보안·무결성 — v0.14.1

이 문서는 현재 작업본의 구현과 검증 범위를 설명합니다. 운영 환경을 적용/조회한 기록이 아닙니다.
재현 명령은 ../README.md에 있습니다. 소스 ZIP에는 과거 실행 로그·검증 보고서를 포함하지 않습니다.
전체 프론트 연결 계약은 API_GUIDE.html과 openapi.json에 있습니다.

| 요구 | 현재 구현 | 검증 / 한계 |
|---|---|---|
| 입력 검증 | 모든 /api/v1 경로 공통 검증. 본문 32KiB, 중복 키/쿼리/헤더·미지정 필드·잘못된 타입 거절 | 전체 가상 API 검사. /docs는 개발 문서 경로이며 production에서 차단 |
| Google 신원 | Supabase /auth/v1/user에 정확한 토큰 검증. metadata로 관리자 판정 금지 | 대체 upstream 검사. 실제 OAuth 미검증 |
| 세션 | 서버 create_dt부터 6h 고정. 재등록/refresh로 연장 불가, 만료·철회 영구 종료. native session 일치/존재 확인 | 가상 API 회귀. 실제 auth.sessions 권한/조회는 실연동 필요 |
| 관리자 | DB type=2 및 운영 MFA aal2. 브라우저 type/aal 주장 직접 신뢰 안 함 | 대체 검증 및 기존 제한 역할 SQL 검사. 운영 계정 미검증 |
| 포인트 | 시즌별 불변 단일 원장. member+season+match 참조, 초기 지급 한 번, 배팅 한 번, 차감 없는 지급/이중 지급/환불 금지 | SQLite 의미 검사; 실제 PostgreSQL 트리거/제약은 별도 필요 |
| DB 권한 | RLS, 제한 LOGIN, 직접 원장/잔액/관리자 변경 차단, private definer 함수 search_path=pg_catalog | 정적 SQL 검토 및 readiness 코드. 운영 실제 정책 적용 확인 아님 |
| TLS | production verify-full 강제, URL의 강한 SSL 모드도 유지 | 설정 회귀. 실제 CA·DNS·nginx HTTPS 미검증 |
| 잠금 | 등록 gate→active seasons→등록 identity→session→member. 사용자 season→match→session→member→contract. 대상 시즌별 작업 | 실행 경로의 SQL 잠금 순서 회귀. 실제 PostgreSQL 동시성은 별도 검증 필요 |
| ORM 재조회 | 잠금 획득 후 기존 identity-map의 세션·시즌·경기·회원 값을 갱신 | 다른 세션의 철회가 기존 조회 객체에 반영되는 회귀 |
| 감사 DB + 파일 | 시작 기록 선보존, 종료 상태 실제 응답 결과 반영. 실제 JSONL append+fsync, SQLite WAL outbox, DB 중복 전달 차단 | SIGKILL·장애·파일 실패·재처리 회귀 검사 |
| 개인 정보 | 원본 토큰·email·IP·요청 전문 미보존, 검증된 안전 식별자/한정 값만. 상세 오류는 제한 파일에 보존 | 가상 식별자 유출·민감값 제거 검사 |
| 무결성 오류 | 성공 응답 전에 트랜잭션 커밋/롤백. 감사 시작 실패는 업무 차단. 완료 로그 누락은 unknown으로 보존 | 강제 종료가 커밋 여부를 증명한다고 가정하지 않음 |
| 요청 제한 | 호스트 공유 rate bucket, IP·member·session별 제한, 동시 요청/본문/쿼리/헤더/DB 작업 예산 | 가상 동시 파일/요청 검사. 다중 호스트 전역 rate limiter는 미구현 |
| 공개 가용성 | 익명 last-known snapshot 제한 보관, 오래된 배팅 닫기. 개인/인증/쓰기 캐시 대체 금지 | 프로세스 재시작 보존 없음. 정상 공유 캐시/실측 용량은 미구현 |
| 오류/CORS | 안전한 detail.code/message, no-store, X-Request-ID, 정확한 origin. Retry-After 노출 | 실제 localhost HTTP 검사. 프록시 오류 CORS는 nginx 예시이며 실제 적용 미검증 |
| 배포 분리 | runtime에서 SQL·DDL 생성·시드·문서·테스트 제외, 전체 유지보수 소스 별도 보존 | ZIP 반복 생성·자기 hash 제외·현재 바이트 일치 검사 |

## 유지 조건

수집기별 전역 writer gate는 provider team UPSERT의 역순 경쟁을 막기 위해 유지합니다. 베팅은 이 gate를 잡지 않습니다.
시즌 활성화는 회원 등록 스냅샷의 일관성을 위해 등록과 gate를 공유하며 다른 시즌 베팅의 시즌 행을 잠그지 않습니다.
여러 시즌에서도 같은 회원의 포인트 변경은 회원 단위로 직렬화합니다. 별도 지갑 테이블을 만들지 않습니다.

읽기 API는 포인트 원장을 만들거나 세션을 연장하지 않습니다. 아직 초기 지급이 없는 활성 시즌 잔액 표시와 실제 초기 지급은 구별합니다.
상태와 신선도 검사는 서버의 금융 승인 시 다시 수행합니다. 무결성 오류에 자동 잔액 보정·권한 확대·원장 수정은 없습니다.

## 로그 보존

LOG_DIRECTORY/archive/*.jsonl에 실제 상세 파일을 씁니다. UTC 일자/프로세스/용량 단위 세그먼트이며 기존 파일을 덮어쓰지 않습니다.
파일은 자동 삭제하지 않습니다. AUDIT_RETENTION_DAYS는 전달 완료 outbox에만 적용됩니다.
started만 남은 이벤트는 완료/커밋을 추정하지 않고 completion_unknown으로 남깁니다.
POSIX private directory와 symlink 방지는 파일 보존 코드에 한정합니다. 사용자 컴퓨터 권한을 요청/확대하는 작업은 하지 않습니다.
운영 파일의 보관 위치·접근 권한·삭제 절차는 실제 호스트에서 관리합니다.

## 현재 미완료 검증

실제 PostgreSQL의 migration/RLS/trigger/잠금 동시성, Google OAuth, TLS와 공급자 계정 호출은 실연동 검증이 필요합니다.
가상 DB·대체 인증을 사용하는 회귀 검사로 해당 항목의 완료를 판단하지 않습니다.

프로젝트 소유의 격리 DB에서 테스트만 수행합니다. 라이엇을 포함한 제3자 사이트에 의도적 부하·스트레스 테스트·대량/병렬 호출·제한 우회를 하지 않습니다.
현재 작업에서 외부 서비스에 테스트 요청을 보내지 않았고 실제 자동 수집은 변경하지 않았습니다.
