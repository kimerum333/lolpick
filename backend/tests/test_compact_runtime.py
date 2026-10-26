"""Cross-layer regressions for compact storage and unchanged public boundaries."""

from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select, text
from app.models import Member, MemberSession, SystemLog, Season, Match
from app.db.compact import key_hash
from app.services.sessions import register_session
from app.core.auth import AuthenticatedUser
from app.services.members import synchronize_member
from app.services.points import apply_match_points
from demo_seed import seed_demo
from app.services.catalog import list_matches
from app import telemetry
from compact_fixtures import internal, lookup
import pytest


def test_finished_filter_excludes_publicly_settled_matches(db):
    seed_demo(db)
    row = db.scalar(select(Match).where(Match.public_code == "demo-w5"))
    row.settlement_status = "settled"
    row.settlement_at = datetime.now(timezone.utc)
    row.settlement_pool = row.settlement_paid = 0
    db.flush()
    finished = list_matches(db, None, "finished", 50, 0)
    assert {item.id for item in finished.items} == {"demo-m1"}
    assert all(item.status == "finished" for item in finished.items)
    settled = list_matches(db, "worlds-2026", "settled", 50, 0)
    assert [item.id for item in settled.items] == ["demo-w5"]
    assert settled.items[0].status == "settled"


def test_draw_provider_flag_survives_compaction_without_final_result(db):
    from types import SimpleNamespace
    from app.services.pandascore_sync import store_display
    from app.services.catalog import serialize_match

    seed_demo(db)
    row = db.scalar(select(Match).where(Match.public_code == "demo-w1"))
    fixture = SimpleNamespace(api_data={"normalized": {"draw": True, "source_status": "finished", "issues": []}})
    store_display(row, fixture, ["opponents_tbd"])
    row.status = "finished"
    row.result = None
    db.flush()
    db.expire(row)
    output = serialize_match(row, (0, 0), datetime.now(timezone.utc), season_status="active")
    assert output.draw is True and output.result is None
    assert output.bettingOpen is False
    assert set(output.dataIssues) == {"draw", "opponents_tbd"}


def test_numeric_storage_keeps_external_auth_and_session_uuids(db, monkeypatch):
    now = datetime.now(timezone.utc)
    identity = AuthenticatedUser(uuid4(), "compact-google", uuid4(), now)
    member = synchronize_member(db, identity, operation_id=uuid4())
    session = register_session(db, identity, native_created=now)
    db.flush()
    assert type(member.id) is int and type(session.id) is int
    assert member.auth_user_id == identity.user_id
    assert session.auth_session_id == identity.session_id
    assert session.member_id == member.id
    stored = db.execute(text("SELECT status FROM lolpick_member_sessions")).scalar_one()
    assert type(stored) is int and stored == 1
    assert db.scalar(select(MemberSession.status)) == "active"


def test_public_codes_stay_strings_while_internal_relations_are_numeric(db):
    seed_demo(db)
    db.commit()
    page = list_matches(db, "worlds-2026", None, 50, 0, team_id="GEN")
    assert page.total == 2
    for item in page.items:
        assert item.season == item.event == "worlds-2026"
        assert isinstance(item.id, str) and "GEN" in item.teams
    row = db.scalar(select(Match).where(Match.public_code == "demo-w1"))
    assert all(type(value) is int for value in (row.id, row.season_id, row.team_a_id, row.team_b_id))
    assert "raw" not in row.api_data
    assert "api_data" not in Match.__table__.c


def test_ledger_digest_is_binary_and_match_command_uses_numeric_context(db):
    from compact_fixtures import make_match

    db.add(Season(public_code="s", slug="s", year=2026, name="S", status="active", initial_points=100))
    db.flush()
    db.add(
        make_match(
            db,
            id="m",
            season_id="s",
            api_source="test",
            external_match_id="m",
            round="BO3",
            team_a_id="A",
            team_b_id="B",
            starts_at=datetime.now(timezone.utc) + __import__("datetime").timedelta(days=1),
            api_data={"normalized": {"source_status": "not_started", "issues": []}},
            status="scheduled",
        )
    )
    member = Member(auth_user_id=uuid4(), google_sub="compact-bettor")
    db.add(member)
    db.flush()
    entry = apply_match_points(
        db,
        member_id=member.id,
        match_id=internal(db, Match, "m"),
        selected_side=1,
        amount=-10,
        reason="bet_stake",
        idempotency_key="stake",
    )
    assert entry.idempotency_key_hash == __import__("app.db.compact", fromlist=["point_key_hash"]).point_key_hash(member.id, entry.season_id, "bet_stake", "stake") and len(entry.idempotency_key_hash) == 32
    assert member.current_points == 90 and entry.selected_side == 1
    reason = db.execute(
        text("SELECT reason FROM lolpick_point_transactions WHERE id=:id"), {"id": entry.id}
    ).scalar_one()
    assert reason == 5


def test_audit_replays_legacy_uuid_actor_and_compacts_request_detail(db, monkeypatch):
    member = Member(auth_user_id=uuid4(), google_sub="legacy-journal")
    db.add(member)
    db.commit()
    monkeypatch.setattr(telemetry, "get_audit_engine", lambda: db.get_bind())
    request_id = uuid4()
    event = dict(
        actor_member_id=str(member.auth_user_id),
        request_id=str(request_id),
        event_key=f"http:{request_id}:finish",
        event_name="http.request",
        source="api",
        phase="finished",
        api_path="/api/v1/matches/{match_id}",
        http_method="GET",
        status_code=404,
        severity="warning",
        outcome="rejected",
        response_complete=True,
        message={"code": "match_not_found", "error_type": "LookupError"},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    assert telemetry.persist_http_event(event) is True
    assert telemetry.persist_http_event(event) is True
    row = db.scalar(select(SystemLog))
    assert row.actor_member_id == member.id and row.event_key_hash == key_hash(event["event_key"])
    assert row.event_code == 2 and row.route_code == 7 and row.phase == "finished"
    assert row.error_code == "match_not_found" and len(row.detail_sha256) == 32
    assert row.message is None and row.context == {"response_complete": True}


def test_worker_failure_and_unregistered_events_have_explicit_compact_mapping(db, monkeypatch):
    monkeypatch.setattr(telemetry, "get_audit_engine", lambda: db.get_bind())
    request_id = uuid4()
    event = dict(
        request_id=str(request_id),
        event_key=f"worker:{request_id}:finish",
        event_name="matches.synced",
        source="system",
        phase="failed",
        severity="error",
        outcome="failure",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    assert telemetry.persist_http_event(event) is True
    row = db.scalar(select(SystemLog))
    assert row.event_code == 35 and row.phase == "finished" and row.outcome == "failure"
    event.update(event_key=f"legacy:{request_id}", event_name="old.worker.event", phase="unknown", feature="matches")
    assert telemetry.persist_http_event(event) is True
    unknown = db.scalar(select(SystemLog).where(SystemLog.event_code == 900))
    assert unknown.context["legacy_event_name"] == "old.worker.event"


def test_analytics_derives_features_and_methods_without_duplicate_storage(db):
    from datetime import timedelta
    from app.services.analytics import feature_usage

    now = datetime.now(timezone.utc)
    for route, method in ((3, "GET"), (6, "GET"), (6, "POST")):
        db.add(
            SystemLog(
                event_code=2,
                source="api",
                outcome="success",
                severity="info",
                route_code=route,
                http_method=method,
                created_at=now,
            )
        )
    db.add(SystemLog(event_code=36, source="system", outcome="success", severity="info", created_at=now))
    db.add(SystemLog(event_code=37, source="system", outcome="success", severity="info", created_at=now))
    db.flush()
    rows = feature_usage(db, now - timedelta(seconds=1), now + timedelta(seconds=1))
    assert {(row["feature"], row["action"]) for row in rows} == {
        ("teams", "get"),
        ("matches", "get"),
        ("matches", "post"),
        ("matches", "sync"),
    }
    assert all(row["total"] == 1 for row in rows)
    assert "feature" not in SystemLog.__table__.c and "action" not in SystemLog.__table__.c


def test_admin_provisioning_never_falls_back_to_runtime_credential(monkeypatch):
    from app import admin_role
    from app.core.config import Settings, DatabaseConfigurationError

    monkeypatch.setattr(admin_role, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(admin_role, "create_engine", lambda *a, **k: pytest.fail("Runtime must not provision admins"))
    monkeypatch.setattr("sys.argv", ["admin_role", str(uuid4())])
    with pytest.raises(DatabaseConfigurationError, match="MIGRATION_DATABASE_URL"):
        admin_role.main()


def test_existing_smallint_provider_is_read_without_consuming_identity(db):
    from sqlalchemy import event
    from app.services.teams import preserve_source

    inserts = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT") and "lolpick_api_sources" in statement:
            inserts.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        first = preserve_source(db, "pandascore")
        for _ in range(50):
            assert preserve_source(db, "pandascore").id == first.id
        assert len(inserts) == 1
    finally:
        event.remove(engine, "before_cursor_execute", capture)


@pytest.mark.parametrize("revoke", [False, True])
def test_admin_cli_resolves_external_uuid_to_internal_role_and_audit(db, monkeypatch, revoke):
    from app import admin_role
    from app.core.config import Settings

    member = Member(auth_user_id=uuid4(), google_sub="operator-cli")
    db.add(member)
    db.flush()
    if revoke:
        member.type = 2
    db.commit()
    auth_uuid, member_id = member.auth_user_id, member.id
    db.commit()
    engine = db.get_bind()
    monkeypatch.setattr(engine, "dispose", lambda: None)
    monkeypatch.setattr(admin_role, "create_engine", lambda *a, **k: engine)
    monkeypatch.setattr(
        admin_role,
        "get_settings",
        lambda: Settings(_env_file=None, migration_database_url="postgresql://operator:p@127.0.0.1/lolpick_test"),
    )
    monkeypatch.setattr("sys.argv", ["admin_role", str(auth_uuid)] + (["--revoke"] if revoke else []))
    admin_role.main()
    db.expire_all()
    assert db.get(Member, member_id).type == (1 if revoke else 2)
    event = db.scalar(select(SystemLog))
    assert event.actor_member_id == member_id and event.event_code == 21
    assert event.context == {"reason": "revoke" if revoke else "grant"}
