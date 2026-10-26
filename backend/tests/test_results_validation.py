from compact_fixtures import internal, lookup, make_match, place_wager
from app.models import Season, Match, Member
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.models import PointTransaction
from app.services.catalog import get_match
from app.services.points import PointOperationError, activate_season, apply_points, finish_season, open_member_season
from app.services.settlement import process_result, record_result
from app.telemetry import redact


@pytest.fixture
def funded(db, request):
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
    match = make_match(
        db,
        id="m1",
        season_id=season.id,
        api_source="test",
        external_match_id="1",
        round="BO3",
        team_a_id="A",
        team_b_id="B",
        starts_at=datetime.now(timezone.utc) + timedelta(days=1),
        status="scheduled",
        api_data={"normalized": {"source_status": "not_started", "issues": []}},
    )
    db.add(match)
    db.flush()
    people, bets = ([], [])
    pairs = [("A", 1), ("A", 2), ("A", 7)] if request.node.name == "test_no_participants_on_winning_team_refunds_everyone" else [("A", 1), ("A", 2), ("B", 7)]
    for index, (team, amount) in enumerate(pairs):
        person = Member(auth_user_id=uuid4(), google_sub=f"subject-{index}")
        db.add(person)
        db.flush()
        open_member_season(db, person, season)
        bet = place_wager(db, member_id=person.id, season_id=season.id, match_id=match.id,
            selected_team_id=team, amount=amount, idempotency_key=f"stake-{index}")
        people.append(person)
        bets.append(bet)
    db.commit()
    return (season, match, people, bets)


def test_winner_payout_floors_and_removes_remainder_once(db, funded):
    _, match, people, bets = funded
    with db.begin():
        first = process_result(db, "m1", "team_a_win", score_a=2, score_b=1)
    assert first["pool"] == 10 and first["paid"] == 9 and (first["removedRemainder"] == 1)
    assert [bet.payout for bet in bets] == [3, 6, 0]
    assert [bet.status for bet in bets] == ["won", "won", "lost"]
    assert [person.current_points for person in people] == [102, 104, 93]
    assert (
        match.status == "finished"
        and match.settlement_status == "settled"
        and match.result == "team_a_win"
        and match.winner_team_id == match.team_a_id
    )
    count = db.scalar(select(func.count()).select_from(PointTransaction))
    recorded_at, settled_at = (match.result_recorded_at, match.settled_at)
    db.commit()
    with db.begin():
        assert process_result(db, "m1", "team_a_win", score_a=2, score_b=1) == first
    assert db.scalar(select(func.count()).select_from(PointTransaction)) == count
    assert match.result_recorded_at == recorded_at and match.settled_at == settled_at
    for person in people:
        assert (
            db.scalar(
                select(func.sum(PointTransaction.amount)).where(
                    PointTransaction.member_id == internal(db, Member, person.id),
                    PointTransaction.season_id == internal(db, Season, "s1"),
                )
            )
            == person.current_points
        )
    payouts = list(db.scalars(select(PointTransaction).where(PointTransaction.reason == "bet_payout")))
    assert {entry.member_id for entry in payouts} == {people[0].id, people[1].id}
    assert all((entry.amount > 0 and entry.match_id == internal(db, Match, "m1") and entry.related_entry_id for entry in payouts))
    db.commit()
    with pytest.raises(PointOperationError, match="finalized"):
        with db.begin():
            process_result(db, "m1", "team_b_win")
    assert match.winner_team_id == match.team_a_id


@pytest.mark.parametrize("result", ["draw", "cancelled", "team_b_win"])
def test_refunds_or_other_winner_are_atomic_and_retryable(db, funded, result):
    _, match, people, bets = funded
    with db.begin():
        outcome = process_result(db, "m1", result)
    assert outcome["pool"] == outcome["paid"] == 10 and outcome["removedRemainder"] == 0
    if result == "team_b_win":
        assert [bet.status for bet in bets] == ["lost", "lost", "won"]
        assert [bet.payout for bet in bets] == [0, 0, 10]
    else:
        assert all((bet.status == "refunded" and bet.payout == bet.amount for bet in bets))
        assert [person.current_points for person in people] == [100, 100, 100]
        assert match.refunded_at and (not match.settled_at)
        assert outcome["settlementStatus"] == "refunded"
    db.commit()
    with db.begin():
        assert process_result(db, "m1", result) == outcome
        finish_season(db, "s1")
    assert lookup(db, Season, "s1").status == "finished"


def test_no_participants_on_winning_team_refunds_everyone(db, funded):
    _, match, people, bets = funded
    assert all(bet.selected_side == 1 for bet in bets)
    db.commit()
    with db.begin():
        outcome = process_result(db, "m1", "team_b_win")
    assert outcome["result"] == "team_b_win" and outcome["winner"] == "B"
    assert outcome["settlementStatus"] == "refunded"
    assert all((bet.status == "refunded" for bet in bets))
    assert all((person.current_points == 100 for person in people))
    assert match.status == "finished"


@pytest.mark.parametrize("status", ["upcoming", "finished"])
def test_season_status_blocks_display_result_and_money_before_processing(db, funded, status):
    season, match, people, _ = funded
    season.status = status
    db.commit()
    with pytest.raises(PointOperationError, match="active season"):
        with db.begin():
            process_result(db, "m1", "team_a_win")
    assert match.result is None and match.status == "scheduled"
    assert [person.current_points for person in people] == [99, 98, 93]
    output = get_match(db, "m1")
    assert output.seasonStatus == status and (not output.bettingOpen)


def test_missing_stake_rolls_back_result_and_all_balances(db, funded):
    _, match, people, bets = funded
    people[1].current_points += 1  # Explicit corruption on SQLite; native role cannot update balances.
    db.commit()
    with pytest.raises(PointOperationError, match="balance"):
        with db.begin():
            process_result(db, "m1", "team_a_win")
    assert match.result is None and match.settlement_status == "pending"
    assert [person.current_points for person in people] == [99, 99, 93]
    assert all((bet.status == "pending" for bet in bets))


def test_balance_overflow_cannot_partially_pay_winners(db, funded):
    from app.db.domain import MAX_POINTS

    _, match, people, bets = funded
    person = people[1]
    apply_points(
        db,
        member_id=internal(db, Member, person.id),
        season_id=internal(db, Season, "s1"),
        amount=MAX_POINTS - person.current_points,
        reason="admin_adjustment",
        idempotency_key="raise-limit",
    )
    db.commit()
    with pytest.raises(PointOperationError, match="point limit"):
        with db.begin():
            process_result(db, "m1", "team_a_win")
    assert people[0].current_points == 99 and people[1].current_points == MAX_POINTS
    assert match.result is None and all((bet.status == "pending" for bet in bets))


def test_winner_is_derived_and_database_rejects_inconsistent_totals(db, funded):
    _, match, _, _ = funded
    with pytest.raises(AttributeError):
        match.winner_team_id = match.team_b_id
    assert "winner_team_id" not in Match.__table__.c
    db.commit()
    with db.begin():
        process_result(db, "m1", "team_a_win")
    assert match.winner_team_id == match.team_a_id
    assert match.settlement_remainder == match.settlement_pool - match.settlement_paid
    db.commit()
    with pytest.raises(IntegrityError):
        with db.begin():
            match.settlement_paid = match.settlement_pool + 1
            db.flush()


def test_result_api_validates_and_settles_with_server_owned_team_ids(client, db, funded):
    from app.api.auth import require_server_session
    from app.core.auth import AuthenticatedUser

    person = funded[2][0]
    user = AuthenticatedUser(person.auth_user_id, person.google_sub, aal="aal2")
    lookup(db, Member, user.user_id).type = 2
    db.commit()
    client.app.dependency_overrides[require_server_session] = lambda: user
    response = client.post("/api/v1/admin/matches/m1/result", json={"result": "team_a_win", "score_a": 2, "score_b": 1})
    assert response.status_code == 200 and response.json()["removedRemainder"] == 1
    assert client.post("/api/v1/admin/matches/m1/result", json={"result": "draw"}).status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        3,
        "x",
        {},
        {"result": None},
        {"result": []},
        {"result": "unknown"},
        {"result": "team_a_win", "score_a": "2", "score_b": 1},
        {"result": "team_a_win", "score_a": True, "score_b": 0},
        {"result": "team_a_win", "score_a": 2},
        {"result": "draw", "score_a": 2, "score_b": 1},
        {"result": "team_a_win", "score_a": 1, "score_b": 2},
        {"result": "cancelled", "score_a": 0, "score_b": 0},
        {"result": "team_a_win", "winner_team_id": "forged"},
    ],
)
def test_result_input_rejected_before_authentication_or_business_code(client, body):
    from app.api.auth import require_server_session
    import json

    client.app.dependency_overrides[require_server_session] = lambda: pytest.fail(
        "Invalid input reached authentication"
    )
    response = client.post(
        "/api/v1/admin/matches/m1/result", content=json.dumps(body), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/ready"),
        ("GET", "/api/v1/teams"),
        ("GET", "/api/v1/seasons"),
        ("GET", "/api/v1/events"),
        ("GET", "/api/v1/matches"),
        ("GET", "/api/v1/matches/m1"),
        ("GET", "/api/v1/auth/me"),
        ("POST", "/api/v1/auth/session"),
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/v1/admin/seasons/s1/refresh"),
        ("POST", "/api/v1/admin/seasons/s1/finish"),
        ("POST", "/api/v1/admin/seasons/s1/activate"),
        ("POST", "/api/v1/admin/matches/m1/result"),
    ],
)
def test_every_application_api_rejects_unknown_query_parameters(client, method, path):
    assert client.request(method, path, params={"unrecognized": "input"}).status_code == 422


@pytest.mark.parametrize(
    "query",
    [
        "limit=oops",
        "limit=-1",
        "limit=101",
        "offset=-1",
        "is_demo=oops",
        "season_id=",
        "season_id=s1&event_id=s2",
        "status=unknown",
        "limit=1&limit=2",
        "team_id=%00",
        "offset=999999999999999999999999999999",
    ],
)
def test_query_format_and_scope_validation(client, query):
    assert client.get("/api/v1/matches?" + query).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b'{"loc":null,"result":[]}',
        b'{"result":"draw","result":"cancelled"}',
        b'{"result":NaN}',
        b'{"score_a":1e999}',
        b"\xff",
        b'{"x":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}",
    ],
)
def test_malformed_inputs_do_not_raise_unhandled_format_or_null_errors(client, body):
    response = client.post(
        "/api/v1/admin/matches/m1/result", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422


def test_body_limits_and_media_type(client):
    path = "/api/v1/admin/matches/m1/result"
    assert client.post(path, content=b"x" * 32769, headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post(path, content=b'{"result":"draw"}', headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post(path, content=b"{}").status_code == 415
    assert (
        client.post(
            path, content=b"{}", headers={"Content-Type": "application/json", "Content-Length": "1"}
        ).status_code
        == 422
    )
    assert client.get("/api/v1/health", headers={"Content-Length": "invalid"}).status_code == 422
    for location in (None, 1, True, "password"):
        assert redact({"loc": location, "password": "secret"})["password"] == "[REDACTED]"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"idempotency_key": 5},
        {"idempotency_key": []},
        {"reason": []},
        {"member_id": None},
        {"season_id": None},
        {"amount": None},
        {"amount": True},
    ],
)
def test_point_inputs_raise_controlled_domain_errors(db, funded, kwargs):
    person = funded[2][0]
    params = dict(member_id=person.id, season_id="s1", amount=1, reason="reward", idempotency_key="reward")
    params.update(kwargs)
    with pytest.raises(PointOperationError):
        apply_points(db, **params)


def test_activation_settles_previously_recorded_upcoming_result_without_resetting_twice(db):
    season = Season(
        public_code="s1",
        slug="activation",
        year=2026,
        name="Activation",
        label="Activation",
        status="upcoming",
        initial_points=100,
    )
    db.add(season)
    db.flush()
    match = make_match(
        db,
        id="m1",
        season_id="s1",
        api_source="test",
        external_match_id="1",
        round="BO3",
        team_a_id="A",
        team_b_id="B",
        starts_at=datetime.now(timezone.utc),
        status="finished",
    )
    db.add(match)
    record_result(match, "team_a_win")
    db.commit()
    with db.begin():
        assert activate_season(db, "s1") == 0
    assert match.settlement_status == "refunded" and match.settlement_pool == 0
    db.commit()
    with db.begin():
        activate_season(db, "s1")
        finish_season(db, "s1")


@pytest.mark.parametrize("case", ["win", "draw", "cancelled", "no_winners", "unknown_winner"])
def test_provider_result_automatically_settles_funded_match_and_preserves_retries(db, case):
    from test_pandascore import apply, match_payload

    apply(db, match_payload())
    season = lookup(db, Season, "test-2026")
    season.status, season.initial_points = ("active", 100)
    db.flush()
    people = []
    for index, team in enumerate(["22", "22"] if case == "no_winners" else ["11", "22"]):
        person = Member(auth_user_id=uuid4(), google_sub=f"provider-{index}")
        db.add(person)
        db.flush()
        open_member_season(db, person, season)
        bet = place_wager(db, member_id=person.id, season_id=season.id,
            match_id="pandascore-901", selected_team_id=team, amount=10,
            idempotency_key=f"provider-stake-{index}")
        people.append(person)
    db.commit()
    payload = match_payload(status="canceled" if case == "cancelled" else "finished")
    if case == "draw":
        payload["draw"], payload["winner_id"] = (True, None)
        payload["results"] = [{"team_id": 11, "score": 1}, {"team_id": 22, "score": 1}]
    if case == "unknown_winner":
        payload["winner_id"] = None
    result = apply(db, payload)
    expected = [110, 90] if case == "win" else [90, 90] if case == "unknown_winner" else [100, 100]
    assert result["settled"] == (1 if case == "win" else 0)
    assert result["refunded"] == (0 if case in {"win", "unknown_winner"} else 1)
    assert [person.current_points for person in people] == expected
    repeat = apply(db, payload)
    assert repeat["settled"] == repeat["refunded"] == repeat["conflicts"] == 0
    assert [person.current_points for person in people] == expected
    if case == "unknown_winner":
        match = lookup(db, Match, "pandascore-901")
        assert match.result is None and match.settlement_status == "pending"
    if case == "draw":
        assert apply(db, match_payload(status="finished"))["conflicts"] == 1
        output = get_match(db, "pandascore-901")
        assert output.draw and output.result == "draw" and (output.winner is None)


def test_provider_balance_corruption_preserves_result_without_partial_money(db):
    from test_pandascore import apply, match_payload
    apply(db, match_payload())
    season = lookup(db, Season, "test-2026")
    season.status, season.initial_points = "active", 100
    person = Member(auth_user_id=uuid4(), google_sub="corrupt-provider-balance")
    db.add(person)
    db.flush()
    bet = place_wager(db, member_id=person.id, season_id=season.id,
        match_id="pandascore-901", selected_team_id="11", amount=10, idempotency_key="funded")
    person.current_points += 1
    db.commit()
    assert apply(db, match_payload(status="finished"))["settlement_blocked"] == 1
    match = lookup(db, Match, "pandascore-901")
    assert match.result == "team_a_win" and match.settlement_status == "pending"
    assert person.current_points == 91 and bet.status == "pending"
    assert "settlement_blocked" in match.api_data["normalized"]["issues"]
    from app.models import SystemLog
    blocked = db.scalar(select(SystemLog).where(SystemLog.event_name == "settlement.blocked"))
    assert blocked.outcome == "failure" and blocked.context["error_code"] == "ledger_balance_mismatch"
    assert blocked.context["match_id"] == match.id
