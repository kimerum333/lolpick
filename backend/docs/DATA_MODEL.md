# v7 데이터 모델

업무 테이블은 9개입니다. Alembic 버전 테이블과 조회 뷰는 별도이며 베팅 저장 테이블은 없습니다. 상세 컬럼·제약·트리거는 app/db/schema.sql이 기준입니다.

| 테이블 | 역할 |
|---|---|
| lolpick_api_sources | 공급자 코드 사전 |
| lolpick_seasons | 대회 시즌·상태·초기 포인트 |
| lolpick_members | Google 인증 매핑·type(1 회원, 2 관리자)·마지막 거래 시즌 잔액 캐시 |
| lolpick_member_sessions | 서버 시각 기준 고정 6시간 세션 |
| lolpick_teams | 공급자 고유식별자와 현재 이름·약칭·로고·표시 정보 |
| lolpick_matches | 참가 팀 슬롯·일정·결과와 별도 정산 상태 |
| lolpick_cheer_contracts | 회원/시즌의 응원 계약 |
| lolpick_point_transactions | 차감·지급·환불을 포함한 변경 불가 원장 |
| lolpick_system_logs | 서버 이벤트와 요청 추적 |

내부 PK/FK는 숫자입니다. Supabase 인증/세션 UUID와 공개 API 코드는 외부 식별자로 유지합니다.
팀 이름 변경은 같은 teams 행을 갱신하며 과거 경기에도 최신 이름을 보여 줍니다. 공급자별 팀 ID는 유일합니다.

베팅은 reason=5, amount<0인 원장 행입니다. match_id와 selected_side(1/2)를 함께 저장합니다. 지급 reason=6 / 환불 reason=7은 related_entry_id로 원래 차감 행을 참조합니다. 회원·시즌·경기 일치, 1인 1경기 1차감, 차감별 최종 지급/환불 1개를 제한합니다.

Wager/Bet 및 lolpick_wagers는 조회 전용입니다. 대기/승리/패배/환불 상태와 지급액은 경기 상태와 원장에서 계산합니다. 별도 Bet INSERT/UPDATE/DELETE는 허용하지 않습니다.

대진 및 시작 시간 미정은 NULL로 보존합니다. API teams/teamDetails의 두 슬롯과 startsAt이 nullable입니다. 양 팀·시간·공급자 상태가 확정되고 active 비데모 시즌의 문제 없는 미래 경기일 때만 베팅합니다.

결과와 정산은 구별합니다. 비데모 포인트는 활성 시즌에만 변합니다. 지급은 풀을 넘을 수 없고 최종 정산 합계가 원장과 일치해야 합니다. 무승부·취소·승자 베팅이 없는 경우 원래 차감을 환불합니다. 정산과 차감·잔액·로그는 동일 트랜잭션입니다.

post_points가 잔액 및 balance_after를 갱신합니다. 앱에서 잔액을 이중 갱신하지 않습니다. lock_members는 회원 행 잠금만 수행합니다. 런타임에게 회원 type/잔액 UPDATE 권한을 주지 않습니다. 잠금 순서는 시즌→경기→회원→계약입니다.
