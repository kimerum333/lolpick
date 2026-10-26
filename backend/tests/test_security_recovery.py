from compact_fixtures import internal, lookup
from app.models import Season, Member
from app.db.compact import key_hash, code_for

"""Behavioral failure tests: cancellation, kill, duplicate delivery and corruption."""
import asyncio
import json
import os
import signal
import stat
import subprocess
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from app.audit_journal import Journal, JournalUnavailable, get_journal
from app.core.config import Settings, get_settings
from app.db.session import connection_options
from app.models import PointTransaction, SystemLog
from app.services.points import PointOperationError, apply_points, open_member_season
from app.telemetry import AuditMiddleware, persist_http_event


def sample(phase="finished", request_id=None):
    request_id = request_id or str(uuid4())
    return dict(
        request_id=request_id,
        event_key=f"http:{request_id}:{('start' if phase == 'started' else 'finish')}",
        created_at=datetime.now(timezone.utc).isoformat(),
        phase=phase,
        event_name="http.request.started" if phase == "started" else "http.request",
        http_method="GET",
        api_path="/api/v1/health",
        feature="http",
        status_code=200,
        response_complete=phase == "finished",
        outcome="success",
        severity="info",
    )


def drive(inner, send_override=None):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if send_override:
            await send_override(message)
        messages.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [],
        "query_string": b"email=PRIVATE_EMAIL",
        "state": {},
        "route": SimpleNamespace(path="/test"),
    }
    asyncio.run(AuditMiddleware(inner)(scope, receive, send))
    return messages


def terminal():
    return [json.loads(e) for e in get_journal().export() if json.loads(e).get("phase") == "finished"][-1]


@pytest.mark.parametrize("failure", ["cancel", "raise", "return"])
def test_partial_response_is_failure_even_with_200_headers(failure):

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "raise":
            raise RuntimeError("PRIVATE_EXCEPTION")

    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            drive(inner)
    elif failure == "raise":
        with pytest.raises(RuntimeError):
            drive(inner)
    else:
        drive(inner)
    event = terminal()
    assert event["status_code"] == 200 and event["outcome"] == "failure"
    assert event["response_complete"] is False
    assert (
        event["reason"]
        == {"cancel": "request_cancelled", "raise": "handler_exception", "return": "response_incomplete"}[failure]
    )
    assert "PRIVATE" not in json.dumps(event)


def test_send_failure_does_not_claim_complete_response():

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"end", "more_body": False})

    async def disconnected(message):
        if message["type"] == "http.response.body":
            raise OSError("PRIVATE_CLIENT_ADDRESS")

    from app.telemetry import ResponseStreamError

    with pytest.raises(ResponseStreamError) as raised:
        drive(inner, disconnected)
    import traceback

    assert "PRIVATE_CLIENT_ADDRESS" not in "".join(traceback.format_exception(raised.value))
    assert terminal()["response_complete"] is False
    assert terminal()["outcome"] == "failure"


def test_finisher_failure_is_contained_and_leaves_unknown_start(client, monkeypatch):
    from app import telemetry

    original = telemetry.emit_event

    def writer(event):
        if event["phase"] == "finished":
            raise RuntimeError("PRIVATE_WRITER_ERROR")
        original(event)

    monkeypatch.setattr(telemetry, "emit_event", writer)
    assert client.get("/api/v1/health").status_code == 200
    assert get_journal().summary()["completion_unknown"] == 1
    assert get_journal().summary()["total"] == 1


def test_start_failure_prevents_handler_execution(client, monkeypatch):
    from app import telemetry

    reached = []

    @client.app.post("/would-mutate")
    def handler():
        reached.append(True)
        return {"done": True}

    def unavailable(event):
        raise JournalUnavailable("PRIVATE_STORAGE_ERROR")

    monkeypatch.setattr(telemetry, "emit_event", unavailable)
    response = client.post("/would-mutate")
    assert response.status_code == 503 and response.json()["detail"]["code"] == "audit_unavailable"
    assert reached == [] and "PRIVATE" not in response.text


def test_invalid_payload_omits_pii_and_unknown_keys_from_all_local_destinations(client):
    secret = "PRIVATE_SENTINEL_123"
    response = client.post(
        "/api/v1/auth/session",
        json={
            "email": secret,
            "google_sub": secret,
            "phone": secret,
            secret: {"field": secret},
            "result": secret,
            "score_a": secret,
        },
        params={"unexpected": secret},
    )
    assert response.status_code == 422
    text = "\n".join(get_journal().export())
    assert secret not in text and "google_sub" not in text and ("email" not in text)
    for path in get_settings().log_directory.glob("*.log*"):
        assert secret not in path.read_text()
    assert terminal()["message"]["input"]["body"]["known_fields"]["result"] == {"type": "string", "length": len(secret)}


def test_db_commit_then_lost_local_ack_is_replayed_exactly_once(db, monkeypatch):
    from app import telemetry

    monkeypatch.setattr(telemetry, "get_audit_engine", lambda: db.get_bind())
    journal = get_journal()
    event = sample()
    journal.append(event)
    acknowledge = journal.acknowledge

    def lost_ack(key):
        raise JournalUnavailable("Lost ack")

    monkeypatch.setattr(journal, "acknowledge", lost_ack)
    with pytest.raises(JournalUnavailable):
        journal.replay(persist_http_event)
    assert journal.summary()["pending"] == 1
    assert db.scalar(select(func.count()).select_from(SystemLog)) == 1
    monkeypatch.setattr(journal, "acknowledge", acknowledge)
    assert journal.replay(persist_http_event) == 1
    assert journal.summary()["pending"] == 0
    assert db.scalar(select(func.count()).select_from(SystemLog)) == 1
    assert db.scalar(select(SystemLog)).event_key_hash == key_hash(event["event_key"])


def test_failed_delivery_survives_journal_reopen_and_retries(tmp_path):
    journal = Journal(tmp_path / "journal")
    event = sample()
    journal.append(event)
    assert journal.replay(lambda _: False) == 0
    reopened = Journal(tmp_path / "journal")
    assert reopened.summary()["pending"] == 1
    assert reopened.pending() == []
    with reopened.connection() as db:
        db.execute("UPDATE events SET retry_at=0")
    delivered = []
    assert reopened.replay(lambda e: delivered.append(e) is None) == 1
    assert delivered == [event]


def test_writer_exception_does_not_acknowledge_pending_event():
    journal = get_journal()
    journal.append(sample())

    def fail(event):
        raise RuntimeError("PRIVATE_DATABASE_ERROR")

    assert journal.replay(fail) == 0
    assert journal.summary()["pending"] == 1
    assert "PRIVATE" not in "\n".join(journal.export())


def test_capacity_never_discards_undelivered_events(tmp_path):
    journal = Journal(tmp_path / "small", max_events=2)
    one, two = (sample(), sample())
    journal.append(one)
    journal.append(two)
    with pytest.raises(JournalUnavailable):
        journal.append(sample())
    assert journal.summary()["pending"] == 2
    assert list(journal.export()) == [json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in [one, two]]


def test_retention_prunes_acknowledged_pairs_but_keeps_unknown_start(tmp_path):
    journal = Journal(tmp_path / "retention", retention_days=1)
    start = sample("started")
    finish = sample(request_id=start["request_id"])
    unknown = sample("started")
    for event in [start, finish, unknown]:
        journal.append(event)
        journal.acknowledge(event["event_key"])
    with journal.connection() as db:
        db.execute("UPDATE events SET created=0")
        db.execute("UPDATE metadata SET value=0 WHERE key='last_pruned'")
    journal.append(sample())
    keys = [json.loads(e)["event_key"] for e in journal.export()]
    assert start["event_key"] not in keys and finish["event_key"] not in keys
    assert unknown["event_key"] in keys and journal.summary()["completion_unknown"] == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGKILL and permissions")
def test_sigkill_preserves_started_record_and_does_not_invent_commit_outcome(tmp_path):
    path = tmp_path / "kill"
    event = sample("started")
    code = "import sys,json,signal; from app.audit_journal import Journal; Journal(sys.argv[1]).append(json.loads(sys.argv[2])); print('ready',flush=True); signal.pause()"
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(path), json.dumps(event)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=5)
        journal = Journal(path)
        assert journal.summary() == {"total": 1, "pending": 1, "completion_unknown": 1}
        saved = json.loads(list(journal.export())[0])
        archive = saved.pop("archive_file")
        assert saved == event
        archive_path = path / archive
        assert archive_path.is_file()
        archived = [json.loads(line) for line in archive_path.read_text().splitlines()]
        assert archived[0]["event_key"] == event["event_key"]
        assert archived[0]["phase"] == "started"
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()


@pytest.mark.skipif(os.name != "posix", reason="Use NTFS ACL validation on Windows")
def test_journal_permissions_and_symlink_rejection(tmp_path):
    journal = Journal(tmp_path / "private")
    assert stat.S_IMODE(journal.path.stat().st_mode) == 384
    assert stat.S_IMODE(journal.path.parent.stat().st_mode) == 448
    link = tmp_path / "link"
    link.symlink_to(journal.path.parent, target_is_directory=True)
    with pytest.raises(JournalUnavailable):
        Journal(link)


def test_rate_limit_returns_429_and_trusts_no_forwarded_header(client, monkeypatch):
    monkeypatch.setenv("RATE_PUBLIC_PER_MINUTE", "1")
    get_settings.cache_clear()
    assert client.get("/api/v1/health", headers={"X-Real-IP": "198.51.100.1"}).status_code == 200
    response = client.get("/api/v1/health", headers={"X-Real-IP": "198.51.100.2"})
    assert response.status_code == 429 and response.headers["Retry-After"] == "60"
    assert terminal()["outcome"] == "rejected"
    with get_journal().connection() as db:
        stored = str(db.execute("SELECT * FROM rate_limits").fetchall())
    assert "198.51.100" not in stored and "testclient" not in stored


def test_rate_limit_is_shared_between_processes(tmp_path):
    path = str(tmp_path / "shared")
    Journal(path)
    code = "import sys;from app.audit_journal import Journal;j=Journal(sys.argv[1]);print(sum(j.admit('198.51.100.1','auth',11) for _ in range(10)))"
    children = [
        subprocess.Popen([sys.executable, "-c", code, path], stdout=subprocess.PIPE, text=True) for _ in range(2)
    ]
    try:
        values = [int(child.communicate(timeout=10)[0]) for child in children]
        assert sum(values) == 11
        assert all((child.returncode == 0 for child in children))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


def test_slow_body_receives_408_before_handler(monkeypatch):
    from app.api.validation import RequestEnvelopeMiddleware

    monkeypatch.setenv("REQUEST_BODY_TIMEOUT", "0.05")
    get_settings.cache_clear()
    reached, messages = ([], [])

    async def inner(scope, receive, send):
        reached.append(True)

    async def slow():
        await asyncio.sleep(1)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/v1/auth/session", "headers": [], "state": {}}
    asyncio.run(AuditMiddleware(RequestEnvelopeMiddleware(inner))(scope, slow, send))
    assert reached == [] and messages[0]["status"] == 408
    assert terminal()["outcome"] == "rejected"


def test_concurrency_limit_rejects_without_waiting(monkeypatch):
    from app.api.resource_limits import ResourceLimitsMiddleware

    monkeypatch.setenv("REQUEST_MAX_CONCURRENT", "1")
    get_settings.cache_clear()

    async def scenario():
        held, release = (asyncio.Event(), asyncio.Event())
        calls = []

        async def inner(scope, receive, send):
            held.set()
            await release.wait()
            calls.append(True)

        middleware = ResourceLimitsMiddleware(inner)

        async def receive():
            return {"type": "http.request", "body": b""}

        messages = []

        async def send(message):
            messages.append(message)

        scope = {"type": "http", "path": "/api/v1/health", "client": ("198.51.100.1", 1), "headers": []}
        first = asyncio.create_task(middleware(scope, receive, send))
        await asyncio.wait_for(held.wait(), 1)
        await middleware(scope, receive, send)
        assert messages[0]["status"] == 503 and calls == []
        release.set()
        await first
        assert middleware.inflight == 0 and calls == [True]

    asyncio.run(scenario())


def test_bad_authorization_is_rejected_even_on_public_catalog(client):
    response = client.get("/api/v1/matches", headers={"Authorization": "Basic PRIVATE"})
    assert response.status_code == 401 and response.json()["detail"]["code"] == "invalid_token"


def funded_member(db):
    season = Season(
        public_code="s1", slug="test", year=2026, name="Test", label="Test", status="active", initial_points=100
    )
    db.add(season)
    db.flush()
    member = Member(auth_user_id=uuid4(), google_sub="private-sub")
    db.add(member)
    db.flush()
    open_member_season(db, member, season)
    apply_points(
        db,
        member_id=internal(db, Member, member.id),
        season_id=internal(db, Season, season.id),
        amount=10,
        reason="reward",
        idempotency_key="earned",
    )
    db.commit()
    return member


@pytest.mark.parametrize("request_key", ["earned", "new"])
def test_corrupt_balance_blocks_both_new_write_and_idempotent_retry(db, request_key):
    member = funded_member(db)
    member.current_points = 111
    db.commit()
    with pytest.raises(PointOperationError) as error:
        with db.begin():
            apply_points(
                db,
                member_id=internal(db, Member, member.id),
                season_id=internal(db, Season, "s1"),
                amount=10,
                reason="reward",
                idempotency_key=request_key,
            )
    assert error.value.code == "ledger_balance_mismatch"
    assert lookup(db, Member, member.id).current_points == 111
    assert db.scalar(select(func.count()).select_from(PointTransaction)) == 2


def test_reconciliation_reports_corruption_and_does_not_repair(db):
    from app.reconcile_points import reconcile

    member = funded_member(db)
    assert reconcile(db)["consistent"] is True
    member.current_points = 115
    db.commit()
    result = reconcile(db)
    assert result["issues"] == [
        {"member_id": str(member.id), "season_id": internal(db, Season, "s1"), "code": "ledger_balance_mismatch"}
    ]
    assert member.current_points == 115
    assert "private-sub" not in json.dumps(result)


def test_missing_opening_is_rejected_even_when_sum_matches(db):
    from app.services.points import verify_member_ledger

    member = funded_member(db)
    opening = db.scalar(select(PointTransaction).where(PointTransaction.reason == "season_open"))
    opening.reason = "reward"
    db.flush()
    with pytest.raises(PointOperationError) as error:
        verify_member_ledger(db, member)
    assert error.value.code == "ledger_opening_mismatch"


def test_database_limits_are_numeric_and_separate_for_audit_pool():
    settings = Settings(
        _env_file=None, db_statement_timeout_ms=20000, db_lock_timeout_ms=5000, db_idle_transaction_timeout_ms=11000
    )
    normal = connection_options(settings)
    audit = connection_options(settings, audit=True)
    assert "statement_timeout=20000" in normal["options"] and "lock_timeout=5000" in normal["options"]
    assert "statement_timeout=5000" in audit["options"] and "lock_timeout=1000" in audit["options"]
    assert "idle_in_transaction_session_timeout=11000" in audit["options"]
    assert normal["prepare_threshold"] is None


def test_validation_error_envelope_does_not_echo_input(client):
    response = client.get("/api/v1/matches", params={"limit": "PRIVATE_INVALID_NUMBER"})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_request"
    assert "PRIVATE_INVALID_NUMBER" not in response.text


def test_worker_failure_uses_same_durable_path_when_db_is_down(monkeypatch):
    from app.sync_pandascore import failure_log
    from app import telemetry

    def unavailable():
        raise RuntimeError("PRIVATE_DATABASE_PASSWORD")

    monkeypatch.setattr(telemetry, "get_audit_engine", unavailable)
    operation_id = uuid4()
    get_journal().append(dict(sample("started", str(operation_id)), event_key=f"worker:{operation_id}:start"))
    failure_log("missing-season", RuntimeError("PRIVATE_EXCEPTION"), operation_id)
    events = [json.loads(line) for line in get_journal().export()]
    assert len(events) == 2 and events[-1]["outcome"] == "failure"
    assert events[-1]["event_key"] == f"worker:{operation_id}:finish"
    assert get_journal().summary()["pending"] == 2 and get_journal().summary()["completion_unknown"] == 0
    assert "PRIVATE" not in json.dumps(events)


@pytest.mark.parametrize("at_commit", [False, True])
def test_transaction_deadline_rolls_back_writes_before_next_statement_or_commit(monkeypatch, at_commit):
    from sqlalchemy import create_engine, text
    from app.db import session

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE counters(value INTEGER)"))
        connection.execute(text("INSERT INTO counters VALUES (100)"))
    clock = [0.0]
    monkeypatch.setattr(session, "monotonic", lambda: clock[0])
    session.install_transaction_budget(engine, 1)
    with pytest.raises(session.TransactionDeadlineExceeded):
        with engine.begin() as connection:
            connection.execute(text("UPDATE counters SET value=110"))
            clock[0] = 2.0
            if not at_commit:
                connection.execute(text("SELECT value FROM counters"))
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT value FROM counters")) == 100
    engine.dispose()


def test_dashboard_excludes_admission_events_from_success_counts(db):
    from datetime import timedelta
    from app.services.analytics import feature_usage

    now = datetime.now(timezone.utc)
    for name in ["http.request.started", "http.request"]:
        db.add(
            SystemLog(
                event_code=code_for("event", name), source="api", outcome="success", severity="info", created_at=now
            )
        )
    db.flush()
    rows = feature_usage(db, now - timedelta(seconds=1), now + timedelta(seconds=1))
    assert len(rows) == 1 and rows[0]["total"] == 1 and (rows[0]["event_name"] == "http.request")


@pytest.mark.parametrize("endpoint", ["teams", "matches"])
def test_unknown_season_filter_is_not_copied_to_audit(client, endpoint):
    sentinel = "private-user-marker"
    assert client.get(f"/api/v1/{endpoint}", params={"season_id": sentinel}).status_code == 200
    assert sentinel not in "\n".join(get_journal().export())
    assert terminal()["season_id"] is None


@pytest.mark.parametrize(
    "column,value", [("api_source_id", None), ("api_source_id", 32000), ("external_series_id", None)]
)
def test_malformed_provider_binding_is_rejected_by_database(db, column, value):
    from sqlalchemy.exc import IntegrityError
    from test_pandascore import apply, match_payload

    apply(db, match_payload())
    season = lookup(db, Season, "test-2026")
    setattr(season, column, value)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_large_headers_are_rejected_before_handler(client):
    response = client.get("/api/v1/health", headers={"X-Extra": "x" * 17000})
    assert response.status_code == 431 and terminal()["outcome"] == "rejected"


def test_production_requires_db_audit_copy():
    with pytest.raises(ValueError, match="SYSTEM_LOG_ENABLED"):
        Settings(_env_file=None, app_env="production", system_log_enabled=False)
