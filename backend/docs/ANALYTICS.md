# 감사 로그와 사용 통계 — v0.14.0

DB에는 event_code, route_code, http_method 코드, outcome/severity/source/phase 코드, 내부 actor_member_id와 season_id, 요청 UUID, 바이너리 event_key_hash를 저장한다. SQL 그룹 집계에서 코드를 기능·동작으로 해석한다. HTTP 시작 이벤트와 worker 시작 이벤트는 완료 사용 횟수에서 제외한다.

context는 허용 목록 및 1KB 제한 및 archive_file 참조이다. 상세 입력 구조·redacted 오류는 JSONL 파일과 로컬 journal에 남고 DB에는 detail_sha256으로 참조한다. 상세 내용 자체를 복구할 수 있는 해시가 아니므로 보존된 실제 JSONL 파일이 필요하다. token/email/IP/요청 원문을 DB에 추가하지 않는다.

기존 journal의 UUID actor는 auth_user_id로 조회해 숫자 FK로 연결한다. 이전 시즌 문자열도 public_code로 조회한다. worker phase=failed는 phase=finished/outcome=failure로 변환한다. 알 수 없는 이전 이벤트는 900과 제한된 legacy 이름으로 보존한다. 같은 이벤트 키는 정확한 UTF-8 SHA-256으로 중복 제거한다.

DB 반영 성공 후 commit을 확인해야 journal에서 delivered로 표시한다. 재전송 실패 시 pending을 버리지 않는다. DB 로그 자동 삭제/보관 작업은 아직 없고, journal 한도에서는 요청을 거부할 수 있다. 운영 상태·backlog·디스크를 감시해야 한다. 로그 CLI는 `python -m app.audit_ops status/export/replay`다.

JSONL은 append+fsync, 자동 삭제 없음입니다. outbox는 전달 완료 기록만 정리합니다.
완료 이벤트가 없는 started는 completion_unknown이며 성공/커밋 통계에 포함하지 않습니다.
동시 시즌의 감사 로그는 실제 선택/변경 시즌을 기록하며 로그인 시 여러 활성 시즌이면 season_id=null일 수 있습니다.
