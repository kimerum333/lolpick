from compact_fixtures import internal, lookup, make_match, place_wager
from app.models import Season, Match, Member, MemberSession
from app.db.compact import key_hash

"""Run only on an explicitly disposable localhost database named lolpick_test."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app.core.config import BACKEND_ROOT, get_settings
from app.db.schema import REVISION
from app.models import PointTransaction, SystemLog
from demo_seed import seed_demo
from app.services.points import apply_points


@pytest.mark.postgres
@pytest.mark.parametrize("second_result", ["team_a_win", "team_b_win"])
def test_concurrent_results_settle_once_or_reject_changed_winner(postgres_database, second_result):
    from app.models import Bet
    from app.services.points import PointOperationError, open_member_season
    from app.services.settlement import process_result

    engine, config = postgres_database
    command.upgrade(config, "head")
    with Session(engine) as db, db.begin():
        season = Season(
            public_code="s1",
            slug="results",
            year=2026,
            name="Results",
            label="Results",
            status="active",
            initial_points=100,
        )
        db.add(season)
        db.flush()
        db.add(
            make_match(
                db,
                id="m1",
                season_id="s1",
                api_source="test",
                external_match_id="1",
                round="BO3",
                team_a_id="A",
                team_b_id="B",
                starts_at=datetime.now(timezone.utc) + timedelta(days=1),
                status="scheduled",
                api_data={"normalized": {"source_status": "not_started", "issues": []}},
            )
        )
        db.flush()
        for index, (team, amount) in enumerate([("A", 1), ("A", 2), ("B", 7)]):
            member = Member(auth_user_id=uuid4(), google_sub=f"race-{index}")
            db.add(member)
            db.flush()
            open_member_season(db, member, season)
            place_wager(db, member_id=member.id, season_id=season.id, match_id="m1",
                selected_team_id=team, amount=amount, idempotency_key=f"race-stake-{index}")

    def settle(result):
        try:
            with Session(engine) as db, db.begin():
                return process_result(db, "m1", result)
        except PointOperationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(settle, result) for result in ("team_a_win", second_result)]
        outcomes = [future.result(timeout=15) for future in futures]
    if second_result == "team_a_win":
        assert outcomes[0] == outcomes[1]
    else:
        assert sum((value is not None for value in outcomes)) == 1
    with Session(engine) as db:
        match = lookup(db, Match, "m1")
        assert match.settlement_status == "settled" and match.settlement_pool == 10
        assert match.settlement_paid + match.settlement_remainder == 10
        assert db.scalar(select(func.count()).select_from(SystemLog).where(SystemLog.event_name == "bet.settled")) == 1
        assert db.scalar(select(func.count()).select_from(Bet).where(Bet.status == "pending")) == 0
        assert db.scalar(select(func.sum(Member.current_points))) == 300 - match.settlement_remainder


@pytest.mark.postgres
def test_pandascore_advisory_lock_prevents_competing_commit(postgres_database):
    from app.integrations.pandascore_mapping import normalize_match, normalize_series
    from app.services.pandascore_sync import SyncError, synchronize

    engine, config = postgres_database
    command.upgrade(config, "head")
    game = {"id": 1, "slug": "league-of-legends"}
    series = normalize_series(
        {"id": 700, "league_id": 50, "year": 2026, "full_name": "Synthetic", "videogame": game}, 700
    )
    fixture = normalize_match(
        {
            "id": 901,
            "serie_id": 700,
            "videogame": game,
            "status": "not_started",
            "scheduled_at": "2099-01-01T00:00:00Z",
            "opponents": [{"type": "Team", "opponent": {"id": team, "name": f"Team {team}"}} for team in (11, 22)],
        },
        700,
    )

    def sync():
        with Session(engine) as db, db.begin():
            return synchronize(
                db,
                season_id="test-2026",
                series=series,
                fixtures=[fixture],
                synced_at=datetime.now(timezone.utc),
                create_slug="test",
            )

    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(90520340)"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(SyncError, match="sync_in_progress"):
                pool.submit(sync).result(timeout=10)
    assert sync()["created"] == 1
    with Session(engine) as db:
        row = lookup(db, Match, "pandascore-901")
        assert row.team_a.external_team_id == "11"
        assert row.status == "scheduled" and (not row.is_demo)


@pytest.fixture
def postgres_database(monkeypatch):
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL for PostgreSQL integration tests.")
    url = make_url(raw).set(drivername="postgresql+psycopg")
    if (
        url.database != "lolpick_test"
        or url.host not in {"127.0.0.1", "localhost"}
        or os.environ.get("TEST_ALLOW_SCHEMA_RESET") != "1"
    ):
        pytest.fail("Use localhost disposable lolpick_test and TEST_ALLOW_SCHEMA_RESET=1.")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", raw)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", raw)
    monkeypatch.setenv("DB_SSLMODE", "disable")
    monkeypatch.setenv("SYSTEM_LOG_ENABLED", "false")
    get_settings.cache_clear()
    engine = create_engine(url, hide_parameters=True)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS lolpick CASCADE"))
        yield (engine, config)
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS lolpick CASCADE"))
        engine.dispose()
        get_settings.cache_clear()


@pytest.mark.postgres
def test_new_database_migrations_seed_and_rls(postgres_database):
    engine, config = postgres_database
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with Session(engine) as db, db.begin():
        assert seed_demo(db)["matches"] == 6
    with Session(engine) as db, db.begin():
        assert seed_demo(db)["matches"] == 0
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM lolpick.lolpick_matches")) == 6
        assert connection.scalar(text("SELECT version_num FROM lolpick.lolpick_alembic_version")) == REVISION
        rows = connection.execute(text("""
            SELECT c.relname, c.relrowsecurity FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='lolpick' AND c.relkind='r' AND c.relname <> 'lolpick_alembic_version'
        """)).all()
        assert len(rows) == 9 and all(row.relrowsecurity for row in rows)
    with pytest.raises(RuntimeError, match="cannot be downgraded"):
        command.downgrade(config, "base")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM lolpick.lolpick_alembic_version")) == REVISION


@pytest.mark.postgres
def test_concurrent_posts_in_two_seasons_keep_independent_balances(postgres_database):
    from app.services.points import season_balance
    engine, config = postgres_database
    command.upgrade(config, "head")
    with Session(engine) as db, db.begin():
        seasons = [Season(public_code=f"parallel-{i}", slug=f"parallel-{i}", year=2026,
            name="Parallel", status="active", initial_points=100 if i == 0 else 30) for i in range(2)]
        person = Member(auth_user_id=uuid4(), google_sub="parallel-native")
        db.add_all([*seasons, person])
        db.flush()
        identities = [row.id for row in seasons]
        person_id = person.id

    def post(index):
        with Session(engine) as db, db.begin():
            apply_points(db, member_id=person_id, season_id=identities[index], amount=3 if index == 0 else 7,
                reason="reward", idempotency_key="parallel-native")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(post, i) for i in range(2)]
        for future in futures:
            future.result(timeout=15)
    with Session(engine) as db:
        person = db.get(Member, person_id)
        assert [season_balance(db, person, db.get(Season, identity)) for identity in identities] == [103,37]
        assert db.scalar(select(func.count()).select_from(PointTransaction)) == 4


@pytest.mark.postgres
def test_native_audit_accepts_file_reference_and_new_routes(postgres_database):
    from app.telemetry import persist_http_event
    from unittest.mock import patch
    engine, config = postgres_database
    command.upgrade(config, "head")
    request = uuid4()
    event = dict(event_name="http.request", event_key=f"http:{request}:finish", request_id=str(request),
        created_at=datetime.now(timezone.utc).isoformat(), outcome="success", severity="info", source="api",
        phase="finished", api_path="/api/v1/events/{event_id}", http_method="GET", status_code=200,
        response_complete=True, archive_file="archive/audit-test.jsonl")
    with patch("app.telemetry.get_audit_engine", return_value=engine):
        assert persist_http_event(event) and persist_http_event(event)
    with Session(engine) as db:
        rows = list(db.scalars(select(SystemLog).where(SystemLog.request_id == request)))
        assert len(rows) == 1 and rows[0].route_code == 17
        assert rows[0].context["archive_file"] == "archive/audit-test.jsonl"






@pytest.mark.postgres
def test_concurrent_rewards_do_not_lose_updates(postgres_database):
    engine, config = postgres_database
    command.upgrade(config, "head")
    member_id = uuid4()
    with Session(engine) as db, db.begin():
        db.add(
            Season(
                public_code="s1",
                slug="worlds",
                year=2026,
                name="Worlds",
                label="Worlds",
                status="active",
                initial_points=100,
            )
        )
        db.flush()
        db.add(Member(auth_user_id=member_id, google_sub="concurrent-test"))

    def grant(amount, key):
        with Session(engine) as db, db.begin():
            entry = apply_points(
                db,
                member_id=internal(db, Member, member_id),
                season_id=internal(db, Season, "s1"),
                amount=amount,
                reason="reward",
                idempotency_key=key,
            )
            return entry.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda args: grant(*args), [(10, "r1"), (20, "r2")]))
    assert ids[0] != ids[1]
    with Session(engine) as db:
        assert lookup(db, Member, member_id).current_points == 130
        assert db.scalar(select(func.sum(PointTransaction.amount))) == 130
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda args: grant(*args), [(5, "same"), (5, "same")]))
    assert ids[0] == ids[1]
    with Session(engine) as db:
        assert lookup(db, Member, member_id).current_points == 135


@pytest.mark.postgres
def test_concurrent_first_login_registers_and_opens_balance_once(postgres_database):
    from app.core.auth import AuthenticatedUser
    from app.services.members import synchronize_member

    engine, config = postgres_database
    command.upgrade(config, "head")
    user = AuthenticatedUser(uuid4(), "concurrent-google-account")
    operation_id = uuid4()
    with Session(engine) as db, db.begin():
        db.add(
            Season(
                public_code="s1",
                slug="worlds",
                year=2026,
                name="Worlds",
                label="Worlds",
                status="active",
                initial_points=100,
            )
        )

    def login(_):
        with Session(engine) as db, db.begin():
            member = synchronize_member(db, user, operation_id=operation_id)
            return (member.id, member.current_points)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(login, range(2)))
    assert results[0] == results[1] and results[0][1] == 100 and type(results[0][0]) is int
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Member)) == 1
        assert db.scalar(select(func.count()).select_from(PointTransaction)) == 1
        assert db.scalar(select(func.count()).select_from(SystemLog)) == 2


@pytest.mark.postgres
def test_registration_and_rollover_use_the_same_season_lock_order(postgres_database, monkeypatch):
    from threading import Event
    from app.core.auth import AuthenticatedUser
    from app.services import members
    from app.services.points import activate_season, finish_season

    engine, config = postgres_database
    command.upgrade(config, "head")
    user = AuthenticatedUser(uuid4(), "rollover-google-account")
    with Session(engine) as db, db.begin():
        db.add_all(
            [
                Season(
                    public_code="s1",
                    slug="worlds",
                    year=2026,
                    name="Worlds",
                    label="Worlds",
                    status="active",
                    initial_points=100,
                ),
                Season(
                    public_code="s2",
                    slug="worlds",
                    year=2027,
                    name="Worlds",
                    label="Worlds",
                    status="upcoming",
                    initial_points=20,
                ),
            ]
        )
    opening_started, release_opening, rollover_started = (Event(), Event(), Event())
    original_open = members.open_member_season

    def pause_open(*args, **kwargs):
        opening_started.set()
        assert release_opening.wait(10)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(members, "open_member_season", pause_open)

    def login():
        with Session(engine) as db, db.begin():
            members.synchronize_member(db, user, operation_id=uuid4())

    def rollover():
        rollover_started.set()
        with Session(engine) as db, db.begin():
            finish_season(db, "s1")
            return activate_season(db, "s2")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(login)
        try:
            assert opening_started.wait(10)
            second = pool.submit(rollover)
            assert rollover_started.wait(10)
        finally:
            release_opening.set()
        first.result(timeout=15)
        assert second.result(timeout=15) == 1
    with Session(engine) as db:
        member = lookup(db, Member, user.user_id)
        assert (member.current_season_id, member.current_points) == (internal(db, Season, "s2"), 20)
        assert (
            db.scalar(
                select(func.sum(PointTransaction.amount)).where(
                    PointTransaction.season_id == internal(db, Season, "s1")
                )
            )
            == 100
        )
        assert (
            db.scalar(
                select(func.sum(PointTransaction.amount)).where(
                    PointTransaction.season_id == internal(db, Season, "s2")
                )
            )
            == 20
        )










@pytest.mark.postgres
def test_postgres_session_clock_and_terminal_state_are_immutable(postgres_database):
    from datetime import timedelta
    from sqlalchemy.exc import DBAPIError

    engine, config = postgres_database
    command.upgrade(config, "head")
    member_id, session_id = (uuid4(), uuid4())
    now = datetime.now(timezone.utc)
    with Session(engine) as db, db.begin():
        db.add(Member(auth_user_id=member_id, google_sub="clock-member"))
        db.flush()
        db.add(
            MemberSession(
                auth_session_id=session_id,
                member_id=internal(db, Member, member_id),
                create_dt=now,
                expires_at=now + timedelta(hours=6),
                status="active",
                status_changed_at=now,
            )
        )
    with pytest.raises(DBAPIError), engine.begin() as c:
        c.execute(
            text(
                "UPDATE lolpick.lolpick_member_sessions SET create_dt=create_dt+interval '1 hour', expires_at=expires_at+interval '1 hour'"
            )
        )
    with engine.begin() as c:
        c.execute(text("UPDATE lolpick.lolpick_member_sessions SET status=3,revoked_at=now(),status_changed_at=now()"))
    with pytest.raises(DBAPIError), engine.begin() as c:
        c.execute(text("UPDATE lolpick.lolpick_member_sessions SET status=1,revoked_at=NULL"))


@pytest.mark.postgres
def test_concurrent_stake_with_different_keys_debits_only_once(postgres_database):
    from app.models import Bet
    from app.services.points import PointOperationError

    engine, config = postgres_database
    command.upgrade(config, "head")
    member_id, bet_id = (uuid4(), uuid4())
    with Session(engine) as db, db.begin():
        db.add(
            Season(
                public_code="s1", slug="test", year=2026, name="Test", label="Test", status="active", initial_points=100
            )
        )
        db.flush()
        db.add_all(
            [
                Member(auth_user_id=member_id, google_sub="parallel-stake"),
                make_match(
                    db,
                    id="m1",
                    season_id="s1",
                    api_source="test",
                    external_match_id="1",
                    round="BO3",
                    team_a_id="A",
                    team_b_id="B",
                    starts_at=datetime.now(timezone.utc) + timedelta(days=1),
                    status="scheduled",
                    api_data={"normalized": {"source_status": "not_started", "issues": []}},
                ),
            ]
        )
        db.flush()

    def post(key):
        try:
            with Session(engine) as db, db.begin():
                apply_points(
                    db,
                    member_id=internal(db, Member, member_id),
                    season_id=internal(db, Season, "s1"),
                    match_id=internal(db, Match, "m1"),
                    selected_side=1,
                    amount=-20,
                    reason="bet_stake",
                    idempotency_key=key,
                )
            return "posted"
        except PointOperationError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(post, ["one", "two"])) == ["posted", "rejected"]
    with Session(engine) as db:
        assert lookup(db, Member, member_id).current_points == 80
        assert (
            db.scalar(select(func.count()).select_from(PointTransaction).where(PointTransaction.reason == "bet_stake"))
            == 1
        )


@pytest.mark.postgres
def test_runtime_statement_timeout_cancels_query_and_connection_recovers(postgres_database):
    from app.db.session import connection_options, install_transaction_budget
    from sqlalchemy.exc import DBAPIError

    fixture_engine, config = postgres_database
    command.upgrade(config, "head")
    settings = get_settings().model_copy(update={"db_statement_timeout_ms": 100, "db_lock_timeout_ms": 100})
    engine = install_transaction_budget(
        create_engine(settings.connection_url(), connect_args=connection_options(settings)), 3
    )
    try:
        with pytest.raises(DBAPIError) as error:
            with engine.begin() as connection:
                assert connection.scalar(text("SELECT current_setting('statement_timeout')")) == "100ms"
                connection.execute(text("SELECT pg_sleep(1)"))
        assert error.value.orig.sqlstate == "57014"
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_v7_runtime_role_posts_points_but_cannot_forge_admin_balance_or_sessions(postgres_database):
    from pathlib import Path
    from hashlib import sha256
    from sqlalchemy.exc import DBAPIError
    from app.services.sessions import register_session
    from app.core.auth import AuthenticatedUser

    engine, config = postgres_database
    command.upgrade(config, "head")
    role = "lolpick_v7_runtime_" + uuid4().hex[:16]
    with engine.begin() as connection:
        connection.execute(text(f'CREATE ROLE "{role}" LOGIN'))
        grants = (Path(BACKEND_ROOT) / "docs/runtime-grants.template.sql").read_text(encoding="utf-8")
        grants = grants.replace("lolpick_runtime", role).replace("BEGIN;", "", 1)
        grants = grants.rsplit("COMMIT;", 1)[0]
        connection.execute(text(grants))
    try:
        with Session(engine) as db, db.begin():
            season = Season(public_code="role", slug="role", year=2026, name="Role", status="active", initial_points=100)
            db.add(season)
            db.flush()
            member = Member(auth_user_id=uuid4(), google_sub="role-member")
            db.add(member)
            db.flush()
            season_id, member_id, auth_id = season.id, member.id, member.auth_user_id
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            entry = connection.execute(text(
                "SELECT * FROM lolpick.post_points(:member,:season,1::smallint,100,:key)"
            ), {"member": member_id, "season": season_id, "key": sha256(b"role-opening").digest()}).mappings().one()
            assert entry["balance_after"] == 100
        for sql in (
            f"UPDATE lolpick.lolpick_members SET type=2 WHERE id={member_id}",
            f"UPDATE lolpick.lolpick_members SET current_points=999 WHERE id={member_id}",
            f"INSERT INTO lolpick.lolpick_members(auth_user_id,google_sub,type) VALUES ('{uuid4()}','forged-admin',2)",
            f"INSERT INTO lolpick.lolpick_point_transactions(member_id,season_id,reason,amount,balance_after,idempotency_key_hash) VALUES ({member_id},{season_id},4,10,110,decode(repeat('11',32),'hex'))",
            "TRUNCATE lolpick.lolpick_point_transactions",
        ):
            with pytest.raises(DBAPIError), engine.begin() as connection:
                connection.execute(text(f'SET LOCAL ROLE "{role}"'))
                connection.execute(text(sql))
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            with Session(bind=connection) as db:
                now = datetime.now(timezone.utc)
                user = AuthenticatedUser(auth_id, "role-member", uuid4(), now)
                row = register_session(db, user, native_created=now)
                assert row.expires_at - row.create_dt == timedelta(hours=6)
                from app.services.sessions import revoke_session
                revoke_session(row)
                db.flush()
                db.refresh(row)
                assert row.status == "revoked" and row.revoked_at is not None
                session_id = row.id
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            connection.execute(text(f"UPDATE lolpick.lolpick_member_sessions SET status=1 WHERE id={session_id}"))
        with Session(engine) as db, db.begin():
            connection = db.connection()
            connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            reward = apply_points(db, member_id=member_id, season_id=season_id, amount=10,
                                  reason="reward", idempotency_key="role-reward")
            assert reward.balance_after == 110
            assert db.get(Member, member_id).current_points == 110
        with Session(engine) as db, db.begin():
            db.connection().execute(text(f'SET LOCAL ROLE "{role}"'))
            from app.services.members import synchronize_member
            now = datetime.now(timezone.utc)
            new_user = AuthenticatedUser(uuid4(), "role-new-member", uuid4(), now)
            created = synchronize_member(db, new_user, operation_id=uuid4())
            assert created.type == 1 and created.current_points == 100
            assert created.current_season_id == season_id
        with Session(engine) as db:
            assert db.get(Member, member_id).type == 1
            assert db.scalar(select(func.count()).select_from(PointTransaction)) == 3
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS lolpick CASCADE"))
            connection.execute(text(f'DROP ROLE "{role}"'))


