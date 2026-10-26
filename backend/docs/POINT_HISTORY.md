# v7 포인트 처리

포인트 쓰기는 services.points.apply_points → lolpick.post_points를 사용합니다. 가입/출석/보상/운영자 조정 사유도 동일 원장 구조를 사용하지만 별도 지급 서비스/API 구현 완료를 의미하지 않습니다. 직접 원장 INSERT/UPSERT와 회원 잔액 UPDATE는 런타임 권한에서 제외합니다.

차감은 match_id+selected_side, 지급/환불은 match_id+related_entry_id를 전달합니다. bet_id 인수는 제거했습니다. 차감 행 ID가 베팅의 ID입니다.

financial idempotency hash는 lolpick.points.v7 네임스페이스에 회원·시즌·사유·요청 키를 포함합니다. 같은 키와 payload 재요청은 기존 행을 반환하며 payload가 바뀌면 거절합니다. 기존 감사 로그의 key_hash 인코딩은 유지합니다.

거절 조건과 예상 잔액을 먼저 검사한 뒤 시즌 초기 기록을 만듭니다. 서비스는 commit하지 않습니다. 호출자는 예외 시 전체 트랜잭션을 rollback해야 하며 DB 오류 뒤에 같은 실패 트랜잭션을 계속 사용하면 안 됩니다.

정산은 원래 차감을 읽고 순서대로 회원을 잠근 뒤 총액/소유/잔액을 확인합니다. 비례 지급은 정수 내림이며 나머지는 settlement_pool-settlement_paid로 보존합니다. 패배에는 0포인트 행을 만들지 않습니다.

운영자용 읽기 전용 보고서: python -m app.point_report --season-id <public-code> --match-id <public-code>. 보고서의 betId와 transactionId는 차감 원장 ID입니다. unfundedBets는 구조상 0입니다. 환불 원장 존재 여부로 집계에서 제외합니다.

동시 시즌에서는 member+season 합계와 마지막 balance_after를 검증합니다.
members.current_points/current_season_id는 마지막 거래 시즌의 호환 캐시이며 전체 잔액이 아닙니다.
현재 head는 0010_concurrent_seasons, 프론트 계약은 API_GUIDE.html입니다.
