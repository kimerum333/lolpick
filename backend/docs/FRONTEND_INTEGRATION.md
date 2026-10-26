# 프론트 인계 — v0.14.1

API_GUIDE.html을 먼저 확인합니다. document/design의 18fe7c1 커밋을 기준으로 화면을 매핑했고 디자인을 변경하지 않았습니다.
전체 API 계약은 openapi.json, 호출 코드와 생성 타입은 frontend-api.ts입니다.

## 우선 연결

1. events?year= → 이벤트 목록, events/{id} → 상세 기간/round 집계.
2. matches?event_id=&date= → 한국 날짜별 경기, unscheduled=true → 미정 경기 탭.
3. matches/{id}와 stake-ranking → 경기 정보·팀별 풀·TOP 5.
4. Google 로그인 콜백에서 session 등록; 선택 시즌을 me와 cheer에 명시.
5. 로그인한 경기에서 context 조회 → 응원 선택 → bets 생성 → context/경기 풀/순위 갱신.
6. 마이페이지의 본인 bets 이력 연결. 계정 전환·로그아웃 시 개인 캐시 삭제.

VITE_LOLPICK_API_ROOT는 https://자신의-서버/api/v1 형태입니다. 같은 origin 리버스 프록시도 가능합니다.
getAccessToken은 Supabase SDK의 최신 access_token을 반환합니다. 서버가 실제 신원/세션/권한을 검증합니다.
새 OAuth 로그인만 registerSession을 호출하고 TOKEN_REFRESHED로 6시간 등록 시계를 재설정하려 하지 않습니다.
Idempotency-Key는 새 제출마다 UUID를 생성하고 응답 유실 재시도는 원래 입력/키를 유지합니다.
공개 읽기에는 토큰을 자동 첨부하지 않습니다. 개인 API는 no-store입니다.

## 표시 주의

- startsAt/endsAt/apiSyncedAt은 epoch ms; auth/cheer/bet 시각은 ISO 8601. 한국 시간 표시를 명시합니다.
- 팀과 시각 null은 TBD/미정입니다. 0 풀 비율의 NaN·Infinity를 방지합니다.
- 배팅 가능 여부는 서버 boolean과 reason을 따릅니다. 클라이언트 시각으로 경기 live/finished를 만들지 않습니다.
- 공개 displayName은 null입니다. “참여자”로 표시하고 UUID/Google sub를 대신 노출하지 않습니다.
- 여러 활성 시즌의 me 무선택 null/0은 시즌 미선택이며 포인트 소진이 아닙니다.
- GET bets의 currentPoints/cheer=null로 전역 잔액을 덮어쓰지 않습니다.
- 장애 스냅샷은 오래된 표시이고 배팅이 닫힙니다. 개인 응답을 공개 데이터 캐시로 대체하지 않습니다.

## 아직 없는 계약

정식 닉네임·닉네임 변경·브랜드/대회 분류·정식 단계 관계·전체 사용자 리더보드·출석/보상/탈퇴 API는 없습니다.
stages는 round별 첫/마지막 경기 시작 집계이며 정식 단계의 종료 시각이 아닙니다.
디자인의 시작 10분 전 마감은 미확정으로 현재 서버는 시작 시각 마감입니다.
외부 보상·경품·현금 관련 기능은 만들지 않습니다. 나머지 대문/마이페이지 디자인이 확정되면 계약을 추가 검토합니다.

## 전달·실연동 상태

이 문서/ZIP은 전달 준비물이며 담당자에게 메시지를 전송하지 않았습니다.
운영 API 주소·정확한 프론트 origin·OAuth redirect URL과 실제 DB readiness는 현재 설정되지/확인되지 않았습니다.
21개 연산은 짧은 localhost HTTP+가상 DB로 확인했습니다. 실제 Google·운영 TLS·PostgreSQL 동시성을 통과했다고 해석하지 않습니다.
생성 예제의 s/m1/A는 실데이터가 아니며 실제 API 목록에서 받은 ID로 교체합니다.


## v0.14.1 연결 보완 적용

`frontend-api.ts` 한 파일에 타입·성공 응답 검사기·전송 처리·화면 반영 guard가 포함됩니다. 별도 임시 integration-guards.js를 추가로 복사할 필요가 없습니다.

- `tokenTimeoutMs` 기본 5000, `timeoutMs` 기본 15000. 토큰 조회와 HTTP의 별도 예산입니다.
- `getAuthScope`에는 동기적으로 읽을 수 있는 현재 계정 ID 또는 안정적인 계정/세션 식별자를 반환합니다. access_token 자체를 넣지 않습니다.
- 계정 전환·로그아웃·새 로그인 처리 시작 시 `guard.changeAccount()`와 `api.invalidateAuth()`를 먼저 호출하고 개인 상태를 지웁니다. 일반 TOKEN_REFRESHED는 토큰 전달만 갱신합니다.
- 모든 개인 조회의 화면 반영과 오류 표시는 `createAccountResponseGuard().run()`의 동기 콜백 안에서 처리합니다. 컴포넌트 해제 때도 해당 guard를 무효화합니다.
- `client_auth_changed`는 새 계정 화면에 표시할 오류가 아닙니다. 전송된 POST가 있으면 `outcomeUnknown=true`일 수 있으므로 이전 계정의 작업 키·입력·신원을 별도로 보존합니다. 새 계정 토큰으로 재시도하지 않습니다.
- `onPublicMetadata({path,stale,ageSeconds,requestId})`는 공개 응답의 신선도를 전달합니다. 메타데이터 콜백은 동기적이고 예외를 던지지 않도록 작성합니다. 배팅 허용 최종 판단은 서버의 boolean과 사유를 따릅니다.
- 성공 HTTP 응답도 잘못된 JSON·필수 필드 누락·타입/상태 오류·안전 정수 범위 초과·잘못된 튜플이면 `invalid_response`로 거절합니다. 서버 업무 무결성 검사를 대체하지 않습니다.
- 프록시 HTML 503/429여도 status·requestId·retryAfter를 보존하며, POST의 자동 재시도는 하지 않습니다. 네트워크/타임아웃/응답 유실의 outcomeUnknown을 단순 배팅 실패로 처리하지 않습니다.

인증 상태 이벤트에서 SDK의 비동기 작업을 중첩하지 말고 동기적으로 현재 토큰/계정 캐시를 갱신합니다. 새 OAuth 완료 후 서버 세션 등록은 별도 연결 흐름에서 수행합니다. SDK getSession은 프론트의 토큰 전달용이며 권한 판단은 FastAPI가 검증합니다.

참고: https://supabase.com/docs/reference/javascript/auth-onauthstatechange
