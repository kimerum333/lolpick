# Google 인증과 서버 세션 — 현재 버전

외부 계약은 유지한다. Google OAuth 후 5분 이내 Bearer와 UUID Idempotency-Key로 POST /api/v1/auth/session을 호출한다. body를 보내지 않는다. 응답 memberId는 Supabase auth_user_id UUID, currentSeasonId는 시즌 public_code, currentPoints는 서버 잔액이다.

내부 회원 id와 세션 id는 BIGINT다. 외부 user UUID는 members.auth_user_id, 외부 native session UUID는 member_sessions.auth_session_id에 별도 UNIQUE로 저장한다. token은 저장하지 않는다. Google sub는 신원 일관성을 확인하기 위한 최소 식별자다. 회원/관리자는 members.type의 1/2로 구분한다. 관리자 판정은 서버가 읽은 type을 사용하며, 회원 FK는 내부 member.id를 사용한다.

GET /api/v1/auth/me, POST /api/v1/auth/logout은 검증된 Google identity와 유효한 서버 세션이 필요하다. 서버 세션은 최초 등록부터 6시간이고 refresh나 새 Idempotency-Key로 연장되지 않는다. 만료·철회한 세션은 다시 활성화할 수 없다. native Auth 세션이 사라지면 서버 세션을 철회한다.

session 등록에는 회원/시즌을 요청 JSON으로 지정할 수 없다. 사용자 응원 선택의 seasonId와 배팅의 matchId/teamId는 별도 사용자 API 계약이며 실제 회원 신원은 서버가 결정한다. 공개 조회는 익명도 가능하지만 Bearer가 있으면 인증·세션을 검사한다. 실제 Supabase auth.sessions 조회 권한과 Google 로그인은 실연동 단계에서 검증해야 한다.

동시 활성 시즌에서 me?season_id= 선택을 명시하고 availableSeasonIds/sessionExpiresAt을 사용한다. 전체 프론트 계약은 API_GUIDE.html이다.
