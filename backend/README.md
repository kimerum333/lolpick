# LoLPICK backend v0.14.1

프론트의 경기·이벤트 목록을 실제 API와 연결하기 위한 작업본입니다.
Vue + FastAPI + nginx + Supabase/PostgreSQL 구조와 9개 업무 테이블을 유지합니다.
자동 수집은 1시간 간격이며 실행 중인 운영 수집기를 이 작업에서 변경하지 않았습니다.

## 프론트 전달 자료

- `docs/API_TESTER.html`: 21개 API를 직접 호출하는 독립 HTML 테스트 도구. 주소·토큰·매개변수·JSON 본문 입력, 응답 검사, 쓰기 확인과 중복 방지 키 관리. 개발 프론트와 같은 origin 또는 정확한 CORS 허용 환경에서 사용합니다.
- `docs/API_GUIDE.html`: 독립 실행 API 페이지. 화면별 매핑, 21개 연산, 실제 가상 HTTP 예제, 인증·오류·시간·null 처리.
- `docs/frontend-api.ts`: OpenAPI 기반 타입과 실제 fetch 호출 함수. 공개 조회 중 진행 중인 동일 요청만 합칩니다.
- `docs/openapi.json`: 실행 앱의 현재 계약. 개발 서버의 `/docs`, `/openapi.json`과 동일합니다.
- `docs/FRONTEND_INTEGRATION.md`: 연결 순서와 미확정 정책.
- `docs/API_SECURITY.md` / `.html`: 현재 보안 구현과 검증 한계.

공개 읽기 9개(health/ready 포함), 개인 8개, 관리자 4개입니다.
운영 모드의 Swagger/OpenAPI 공개 차단은 유지합니다. 전달 문서는 runtime 밖에서 제공합니다.

## DB와 환경

현재 DB head는 `0010_concurrent_seasons`입니다. v0.13에서 v0.14로 새 SQL 패치는 없습니다.
기존 DB revision을 먼저 확인하고 `docs/SUPABASE_SETUP.md`의 업그레이드 경로를 따릅니다.
기존 DB에 빈 DB 초기화용 `docs/schema.sql`을 재실행하거나 강제 stamp하지 않습니다.

운영 `.env`·DB·원래 PC에 자동 적용하지 않습니다. 기존 자격 증명과 로그를 보존합니다.
운영 설정은 APP_ENV=production, SYSTEM_LOG_ENABLED=true, ADMIN_REQUIRE_MFA=true,
DB_SSLMODE=verify-full, 제한된 전용 DB LOGIN 역할이어야 합니다. 실제 값은 ZIP에 포함하지 않습니다.

```powershell
# 필요한 설정과 기존 DB revision을 확인한 뒤, 실제 호스트에서 실행
python -m app.check_db
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

`/api/v1/health`는 생존 검사, `/api/v1/ready`는 DB·역할·RLS·필수 함수 권한 검사입니다.
원장 직접 쓰기·회원 역할/잔액 직접 수정 권한은 열지 않습니다.

## 검증

```powershell
# 저장소의 backend/에서, 운영 .env를 읽지 않는 테스트
python -m pytest -q
python -m ruff check app migrations tests scripts
# 가상 DB/인증만 사용하는 짧은 실제 localhost HTTP 검사
python scripts/verify_http_contract.py --output ../verification
python scripts/build_api_docs.py --examples tests/fixtures/api-examples.json
node --test scripts/frontend-api.test.mjs
```

PostgreSQL 통합 검사는 localhost의 버려도 되는 `lolpick_test` DB 및
TEST_DATABASE_URL, TEST_ALLOW_SCHEMA_RESET=1을 명시해야 합니다. 해당 schema를 삭제하므로
운영 연결에서는 실행하지 않습니다. `verify.ps1 -Native`의 격리 Docker 절차도 사용할 수 있습니다.
Google 실제 OAuth·운영 PostgreSQL·nginx TLS·운영 호스트 성능은 별도 실연동 검증 대상입니다.
소스 ZIP에는 과거 실행 로그·검증 보고서를 포함하지 않습니다. 위 명령으로 현재 소스를 검사합니다.
테스트용 데모 생성기는 `tests/demo_seed.py`에만 있으며 운영 DB 연결이나 실행 CLI가 없습니다.
API 예제 JSON은 가상 데이터이며 실제 사용자·운영 DB의 덤프가 아닙니다.

## 패키징

```powershell
python scripts/build_release.py --output ../../lolpick-backend-v0.14.1.zip
```

출력 ZIP은 backend 소스 밖에 생성합니다. runtime에는 실행 Python·실행 의존성·배포 예시만 포함합니다.
SQL, DDL 생성기, 문서, 테스트와 테스트용 가상 데이터, 마이그레이션, 관리 스크립트는 maintenance에 보존합니다.
MANIFEST.json은 현재 바이트만 해시하며 자기 자신은 제외합니다. 같은 출력에 다시 생성해도
이전 manifest/백업/로그를 읽어 넣지 않습니다. 실제 서버 반영은 아직 수행하지 않았습니다.

## 미확정 범위

공개 닉네임, 브랜드/대회 분류/정식 단계 관계, 디자인의 시작 10분 전 마감,
대문·마이페이지의 추가 기능은 별도 확정이 필요합니다. 현재 마감은 경기 시작 시각입니다.
가입/출석/보상 지급 enum은 해당 서비스를 구현했다는 뜻이 아닙니다.
무료 가상 포인트만 사용하며 외부 보상·환전·이체·경품은 없습니다.
라이엇 등 제3자 사이트의 스트레스 테스트·의도적 부하·대량/병렬 호출·제한 우회는 금지합니다.
