"""Concurrent-season money, bounded frontend reads and file audit regressions."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import json
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.models import Match, Member, PointTransaction, Season, SystemLog, Team
from app.services.points import PointOperationError, activate_season, apply_points, finish_season, season_balance
from app.services.settlement import process_result
from app.services.teams import preserve_team
from compact_fixtures import make_match


@pytest.fixture
def two_seasons(db):
    seasons = [Season(public_code=f"parallel-{i}", slug=f"parallel-{i}", year=2026,
        name=f"Parallel {i}", status="active", initial_points=initial)
        for i, initial in enumerate((100, 30))]
    db.add_all(seasons)
    db.flush()
    matches = [make_match(db, id=f"parallel-match-{i}", season_id=season.id, api_source="test",
        external_match_id=f"parallel-match-{i}", round="Final", team_a_id="A", team_b_id="B",
        starts_at=datetime.now(timezone.utc)+timedelta(days=1), status="scheduled",
        api_data={"normalized":{"source_status":"not_started","issues":[]}})
        for i, season in enumerate(seasons)]
    member = Member(auth_user_id=uuid4(), google_sub=str(uuid4()))
    db.add_all([*matches, member])
    db.flush()
    return seasons, matches, member


def stake(db, data, index, amount, key):
    seasons, matches, member = data
    return apply_points(db, member_id=member.id, season_id=seasons[index].id, match_id=matches[index].id,
        selected_side=1, amount=-amount, reason="bet_stake", idempotency_key=key)


def test_interleaved_bets_and_refund_use_their_own_balance(db, two_seasons):
    seasons, matches, member = two_seasons
    first = stake(db, two_seasons, 0, 80, "same-key")
    second = stake(db, two_seasons, 1, 10, "same-key")
    assert first.idempotency_key_hash != second.idempotency_key_hash
    assert [season_balance(db, member, season) for season in seasons] == [20, 20]
    assert stake(db, two_seasons, 0, 80, "same-key").id == first.id
    db.commit()
    with db.begin():
        process_result(db, matches[0].public_code, "cancelled")
    assert [season_balance(db, member, season) for season in seasons] == [100, 20]
    db.commit()
    with db.begin():
        finish_season(db, seasons[0].public_code)
    assert seasons[1].status == "active" and season_balance(db, member, seasons[1]) == 20
    with pytest.raises(PointOperationError):
        apply_points(db, member_id=member.id, season_id=seasons[0].id, amount=1,
            reason="reward", idempotency_key="late")


def test_other_season_cannot_fund_an_insufficient_bet(db, two_seasons):
    stake(db, two_seasons, 0, 1, "one")
    with pytest.raises(PointOperationError, match="Insufficient"):
        stake(db, two_seasons, 1, 31, "over")
    seasons, _, member = two_seasons
    assert season_balance(db, member, seasons[0]) == 99
    assert db.scalar(select(func.count()).select_from(PointTransaction).where(
        PointTransaction.season_id == seasons[1].id)) == 0


def test_activation_keeps_other_season_open_and_preserves_history(db, two_seasons):
    seasons, _, member = two_seasons
    stake(db, two_seasons, 0, 10, "existing")
    third = Season(public_code="third", slug="third", year=2026, name="Third", status="upcoming", initial_points=7)
    db.add(third)
    db.flush()
    assert activate_season(db, "third") == 1
    assert activate_season(db, "third") == 0
    assert seasons[0].status == "active" and season_balance(db, member, seasons[0]) == 90
    assert season_balance(db, member, third) == 7


def test_corrupt_non_cached_season_is_rejected(db, two_seasons):
    stake(db, two_seasons, 0, 10, "a")
    stake(db, two_seasons, 1, 10, "b")
    seasons, _, member = two_seasons
    first = db.scalar(select(PointTransaction).where(PointTransaction.season_id == seasons[0].id,
        PointTransaction.reason == "bet_stake"))
    first.balance_after = 91  # Only the SQLite test DB permits this deliberate corruption.
    db.flush()
    with pytest.raises(PointOperationError, match="Ledger total"):
        season_balance(db, member, seasons[0])


@pytest.fixture
def api():
    from test_user_actions import UserActionsTests
    fixture = UserActionsTests()
    fixture.setUp()
    yield fixture
    fixture.tearDown()


def add_parallel(api):
    with Session(api.engine) as db, db.begin():
        first = db.scalar(select(Match).where(Match.public_code == "m1"))
        season = Season(public_code="other", slug="other", year=2026, name="Other", status="active", initial_points=50)
        db.add(season)
        db.flush()
        db.add(Match(public_code="other-match", season_id=season.id, api_source_id=first.api_source_id,
            external_match_id="other-match", round="Final", team_a_id=first.team_a_id, team_b_id=first.team_b_id,
            starts_at=api.now+timedelta(hours=2), status="scheduled", provider_status="not_started", api_synced_at=api.now))


def test_match_context_is_private_and_does_not_initialize_or_extend_session(api):
    add_parallel(api)
    expiry = api.request("GET", "me?season_id=s").json()["sessionExpiresAt"]
    response = api.request("GET", "matches/other-match/context")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert response.json()["currentPoints"] == 50 and response.json()["reason"] == "cheer_required"
    assert response.json()["sessionExpiresAt"] == expiry
    assert api.scalar(select(func.count()).select_from(PointTransaction)) == 0
    assert api.request("GET", "matches/other-match/context", user=None).status_code == 401


def test_me_requires_season_context_when_multiple_are_active(api):
    add_parallel(api)
    response = api.request("GET", "me")
    assert response.status_code == 200
    assert response.json()["currentSeasonId"] is None and response.json()["currentPoints"] == 0
    assert set(response.json()["availableSeasonIds"]) == {"s", "other"}
    assert api.request("GET", "me?season_id=s").json()["currentPoints"] == 1000
    assert api.request("GET", "me?season_id=other").json()["currentPoints"] == 50
    assert api.request("GET", "me?season_id=missing").status_code == 404
    assert api.request("GET", "me?season_id=s&season_id=other").status_code == 422


def test_bet_returns_balance_and_context_reads_only_own_bet(api):
    assert api.cheer().status_code == 200
    result = api.bet(amount=100).json()
    assert result["currentPoints"] == 900 and result["cheer"]["teamId"] == "A"
    alice = api.request("GET", "matches/m1/context").json()
    bob = api.request("GET", "matches/m1/context", user="bob").json()
    assert alice["bet"]["amount"] == 100 and alice["reason"] == "bet_already_placed"
    assert alice["myRank"] == 1 and bob["myRank"] is None
    assert bob["bet"] is None and bob["currentPoints"] == 1000


def test_event_detail_and_year_filter_are_bounded_contracts(api):
    add_parallel(api)
    response = api.client.get("/api/v1/events/s")
    assert response.status_code == 200 and response.json()["stages"][0]["matchCount"] == 3
    assert api.client.get("/api/v1/events?year=2025").json() == []
    assert len(api.client.get("/api/v1/events?year=2026").json()) == 2
    assert api.client.get("/api/v1/events/missing").status_code == 404
    assert api.client.get("/api/v1/events/s?anything=1").status_code == 422


def test_korean_date_boundaries_and_unscheduled_filter(api):
    for code, value in (("m1", datetime(2026,1,1,14,59,tzinfo=timezone.utc)),
                        ("m2", datetime(2026,1,1,15,tzinfo=timezone.utc)), ("m3", None)):
        api.mutate(Match, Match.public_code == code, starts_at=value)
    assert [row["id"] for row in api.client.get("/api/v1/matches?date=2026-01-02").json()["items"]] == ["m2"]
    assert [row["id"] for row in api.client.get("/api/v1/matches?unscheduled=true").json()["items"]] == ["m3"]
    for query in ("date=17919", "date=2026-02-30", "date=2026-01-02&unscheduled=true", "date=9999-12-31"):
        assert api.client.get("/api/v1/matches?"+query).status_code == 422


def test_public_top_five_never_exposes_member_or_ledger_ids(api):
    api.cheer()
    api.bet(amount=100)
    response = api.client.get("/api/v1/matches/m1/stake-ranking")
    assert response.status_code == 200
    row = response.json()["items"][0][0]
    assert row["amount"] == 100 and row["shareBasisPoints"] == 10000
    assert set(row) == {"rank", "displayName", "amount", "shareBasisPoints", "sealHeld"}
    for user in api.users.values():
        assert str(user.user_id) not in response.text and user.google_sub not in response.text
    assert api.client.get("/api/v1/matches/missing/stake-ranking").status_code == 404


def test_tbd_zero_pool_and_closed_reason(api):
    api.mutate(Match, Match.public_code == "m1", team_a_id=None, starts_at=None)
    detail = api.client.get("/api/v1/matches/m1").json()
    assert not detail["bettingOpen"] and detail["bettingClosedReason"] == "teams_tbd"
    rank = api.client.get("/api/v1/matches/m1/stake-ranking").json()
    assert rank["pools"] == [0,0] and rank["items"] == [[],[]]


def test_provider_missing_fields_preserve_manual_display(db):
    now = datetime.now(timezone.utc)
    team = preserve_team(db, "test", {"id":"display","name":"Old","region":"KR","color":"#123456"}, now)
    preserve_team(db, "test", {"id":"display","name":"New"}, now+timedelta(hours=1))
    db.refresh(team)
    assert team.display_data["region"] == "KR" and team.display_data["color"] == "#123456"
    assert team.name == "New"


def test_file_audit_is_delivered_with_archive_reference_and_registered_route(api, monkeypatch):
    from app import telemetry
    from app.audit_journal import get_journal
    api.client.get("/api/v1/events/s")
    monkeypatch.setattr(telemetry, "get_audit_engine", lambda: api.engine)
    assert get_journal().replay(telemetry.persist_http_event, limit=100) >= 2
    with Session(api.engine) as db:
        row = db.scalar(select(SystemLog).where(SystemLog.route_code == 17, SystemLog.phase == "finished"))
        assert row is not None and row.context["archive_file"].startswith("archive/audit-")
        archive = Path(api.tmp.name) / row.context["archive_file"]
        lines = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines()]
        assert any(line["request_id"] == str(row.request_id) for line in lines)


def test_authenticated_public_response_is_not_cacheable(api):
    response = api.client.get("/api/v1/events/s", headers={"Cookie":"test=1"})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
