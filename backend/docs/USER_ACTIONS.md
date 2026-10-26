# 사용자 베팅·응원 계약 API — v0.14.0

기존 작업본의 사용자 API를 이어받았습니다. 모든 경로는 검증된 Bearer 토큰과 서버에서 관리하는 유효한 Google 로그인 세션을 사용합니다. 회원 ID는 요청 본문에서 받지 않습니다.

| 메서드 | 경로 | 입력 | 결과 |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/cheer` | `seasonId`, `teamId`, UUID 형식 `Idempotency-Key` 헤더 | 시즌 응원 계약 |
| GET | `/api/v1/auth/cheer` | `season_id` 쿼리 | 본인 계약 또는 `null` |
| POST | `/api/v1/auth/bets` | `matchId`, `teamId`, 정수 `amount`, UUID 형식 `Idempotency-Key` 헤더 | 베팅 원장 기반 결과 |
| GET | `/api/v1/auth/bets` | 선택적 `season_id`, `limit`(1–100), `offset` | 본인 이력 `items`, `total`, `limit`, `offset` |

POST 예시:

```json
{"matchId":"public-match-code","teamId":"provider-team-id","amount":100}
```

응원 팀은 첫 베팅 전에 선택해야 하며 시즌 내 변경할 수 없습니다. 같은 회원·시즌에서 동일 키와 동일 입력으로 재시도하면 기존 베팅을 반환합니다. 동일 키의 입력이 달라지거나 새 키로 같은 경기에 다시 베팅하면 거절합니다. 다른 시즌은 별도 키 공간을 사용하므로 프런트에서는 매 작업마다 새 UUID를 발급합니다. 경기 시작 이후에도 기존 성공 요청의 같은 키 재시도는 기존 결과를 조회합니다. 응답은 현재 베팅 상태를 반영하므로 정산 뒤 재조회에서는 상태가 달라질 수 있습니다.

응원 팀이 출전한 경기에서 반대 팀에 베팅하면 인장이 철회됩니다. 이후 응원 팀에 베팅하거나 경기가 취소돼 환불돼도 철회를 되돌리지 않습니다. 응원 팀이 출전하지 않은 경기는 일반 베팅이며 `cheerAction`은 `null`입니다.

잔액 변경·베팅 원장·인장 철회·업무 감사 로그는 같은 트랜잭션에서 처리합니다. 업무 감사 로그 기록 중 예외가 나면 잔액과 인장 변경도 롤백합니다. 요청 자체의 장애 기록은 기존 독립 로컬 감사 저널 경로를 사용합니다. 이 문서는 해당 HTTP 장애 경로의 이번 실행 검증 완료를 의미하지 않습니다.

주요 오류 코드는 `cheer_required`, `cheer_team_locked`, `idempotency_conflict`, `bet_already_placed`, `point_balance_limit`, `betting_closed`, `provider_data_stale`, `session_revoked`, `session_expired`입니다. 금액에 문자열·실수·불리언을 넣거나 회원/계약/응원 행동 필드를 위조하면 입력 모델에서 거절합니다. HTTP 입력 경계 검증은 tests/test_http_boundaries.py에 있습니다.

## 기존 DB 적용과 검증

현재 DB head는 `0010_concurrent_seasons`입니다. v0.13→v0.14에는 새 SQL 패치가 없습니다.
기존 0008/0009 DB는 `SUPABASE_SETUP.md` 및 `CONCURRENT_SEASONS.md`의 기존 업그레이드 경로를 따릅니다.
운영 DB를 조회하거나 변경하지 않았습니다. 기존 DB에 초기화 SQL을 다시 실행하거나 강제 stamp하지 않습니다.

현재 사용자 context 경로는 GET /api/v1/auth/matches/{match_id}/context입니다.
POST /auth/bets 성공 응답의 currentPoints/cheer는 해당 시즌 스냅샷입니다. 이력 GET에서는 null입니다.
여러 시즌의 잔액은 선택한 시즌별로 분리합니다. 자세한 시간·null·상태 계약은 API_GUIDE.html을 참조합니다.

재현 명령은 ../README.md에 있습니다. localhost HTTP 검사에는 가상 SQLite·대체 Google/native session을 사용합니다.
실제 Google/운영 TLS/PostgreSQL 네이티브 잠금·권한 검증을 대체하지 않습니다.
