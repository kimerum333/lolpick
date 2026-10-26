# Supabase 운영 설정 — v0.14.0 / 0010_concurrent_seasons

현재 스키마는 9개 업무 테이블과 단일 시즌별 포인트 원장입니다.
v0.14는 앱/API/문서/패키징 수정이며 v0.13 대비 새 DB migration이 없습니다.
이번 작업에서 실제 프로젝트의 revision·권한·데이터를 조회하거나 변경하지 않았습니다.

## 기존 DB 확인과 업그레이드

```sql
SELECT version_num FROM lolpick.lolpick_alembic_version;
```

- 0010_concurrent_seasons: v0.14용 SQL을 추가 실행하지 않습니다.
- 0009_user_actions: 소유자 연결로 Alembic upgrade head 또는 concurrent-seasons-upgrade.sql 중 하나를 사용합니다.
- 0008_native_session_lookup: Alembic 체인으로 0009→0010을 적용하거나 두 독립 패치를 순서대로 적용합니다.
- 그 외 revision: 지원 경로를 별도 확인합니다. 강제 stamp·원장 자동 보정·현재 초기화 SQL 재실행을 하지 않습니다.
- 완전히 빈 별도 DB에만 docs/schema.sql 또는 Alembic 초기화를 사용합니다.

관리/마이그레이션은 maintenance/backend에서 실행합니다. 운영 runtime에 SQL·Alembic 실행 자료를 복사하지 않습니다.
기존 0010 업그레이드는 원장/회원 캐시 불일치 또는 잠금 시간 초과에서 전체 롤백하며 데이터를 자동 수리하지 않습니다.

## 실제 연결 설정

1. 전용 lolpick_runtime LOGIN 역할을 사용합니다. 소유자 멤버십·superuser·BYPASSRLS·CREATEROLE·CREATEDB·REPLICATION을 허용하지 않습니다.
2. 현재 runtime-grants.template.sql의 최소 권한·RLS 정책을 검토합니다. auth.sessions 정책이나 테이블 직접 권한은 바꾸지 않습니다.
3. DATABASE_URL은 직접 연결 또는 Session pooler 5432입니다. Transaction pooler 6543은 지원하지 않습니다.
4. 운영 APP_ENV=production, SYSTEM_LOG_ENABLED=true, ADMIN_REQUIRE_MFA=true, DB_SSLMODE=verify-full을 사용합니다. 신뢰 CA/호스트 검증을 확인합니다.
5. 서버 SUPABASE_URL·sb_publishable_ 형태 키·PandaScore 토큰을 설정합니다. Google만 로그인 제공자로 사용하고 프론트 정확한 OAuth redirect와 CORS origin을 구성합니다.
6. 실제 호스트에서 app.check_db와 /api/v1/ready를 확인합니다. 이어서 실제 Google 로그인→session 등록→me/context→사용자 쓰기를 확인합니다.

서버는 운영 엔진 최초 사용 시에도 revision·실제 역할·RLS·필수 함수 실행 권한을 검사합니다.
등록 게이트에 쓰는 pg_catalog advisory transaction lock 및 hashtext 실행 권한도 readiness에서 확인합니다.
권한 누락을 owner 연결로 우회하지 않습니다. 프론트에는 DB 연결정보·서비스 역할·공급자 토큰을 주지 않습니다.

## 세션·수집·감사

read_native_session(session_uuid,user_uuid)는 일치하는 auth.sessions의 user_id/created_at만 반환하며 not_after도 확인합니다.
PUBLIC/anon/authenticated 실행을 차단한 private SECURITY DEFINER 함수이며 search_path=pg_catalog입니다.
Google 토큰은 서버에서 검증하고 고정 6시간 세션을 최초 등록합니다. refresh·새 키로 연장하지 않습니다.

자동 수집 간격은 3600초, 신선도 한도 기본 4500초입니다. 운영 중인 수집은 그대로 유지합니다.
--watch는 오류에서 종료하므로 운영 타이머/프로세스 감독 상태는 실제 호스트에서 확인합니다.

LOG_DIRECTORY의 SQLite outbox와 archive/*.jsonl은 실제 파일입니다. JSONL은 append+fsync이며 자동 삭제하지 않습니다.
AUDIT_RETENTION_DAYS는 DB 전달이 끝난 outbox 기록만 대상으로 합니다. 개인 정보/토큰/원본 요청 전문은 보존하지 않습니다.

## 검증 한계

가상 SQLite·대체 인증·대체 공급자로 수행한 API/보안/HTTP 검증은 실제 Supabase의 RLS·네이티브 트리거·동시성을 대체하지 않습니다.
격리 PostgreSQL 검사와 실제 Google 인증·운영 TLS를 별도로 확인해야 합니다.
검증 명령은 ../README.md를 참조합니다. 과거 확인 기록을 현재 환경의 확인으로 해석하지 않습니다.
