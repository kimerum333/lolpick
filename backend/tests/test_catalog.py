from compact_fixtures import internal, lookup, make_match, place_wager, make_entry
from app.models import Season, Match, Member
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.models import Base, Bet, PointTransaction
from demo_seed import seed_demo
from app.services.catalog import serialize_match
from app.services.points import apply_points


def test_seed_stores_team_snapshots_without_master_or_demo_money(db):
    anchor = datetime(2026, 10, 6, 6, 0, tzinfo=timezone.utc)
    assert seed_demo(db, anchor) == {"seasons": 2, "matches": 6}
    db.commit()
    original_time = lookup(db, Match, "demo-w1").starts_at
    assert seed_demo(db, anchor + timedelta(days=1)) == {"seasons": 0, "matches": 0}
    assert lookup(db, Match, "demo-w1").starts_at == original_time
    assert "lolpick.teams" not in Base.metadata.tables
    assert "lolpick.wallets" not in Base.metadata.tables
    assert db.scalar(select(func.count()).select_from(Member)) == 0
    assert db.scalar(select(func.count()).select_from(PointTransaction)) == 0


def test_api_reads_snapshot_and_keeps_event_alias(client, db):
    seed_demo(db)
    db.commit()
    assert len(client.get("/api/v1/teams").json()) == 6
    seasons = client.get("/api/v1/seasons").json()
    assert seasons == client.get("/api/v1/events").json()
    assert {row["id"] for row in seasons} == {"worlds-2026", "msi-2026"}
    page = client.get("/api/v1/matches", params={"season_id": "worlds-2026", "limit": 2}).json()
    assert page["total"] == 5 and len(page["items"]) == 2
    match = next((item for item in client.get("/api/v1/matches").json()["items"] if item["id"] == "demo-w1"))
    assert match["teams"] == ["GEN", "T1"]
    assert match["teamDetails"][0]["name"] == "Gen.G"
    assert match["pools"] == [6480000, 3920000]
    assert isinstance(match["startsAt"], int)
    assert match["poolSource"] == "demo"
    assert match["season"] == match["event"] == "worlds-2026"


@pytest.mark.parametrize(
    "query", [{"limit": 101}, {"offset": -1}, {"status": "wrong"}, {"season_id": "one", "event_id": "two"}]
)
def test_invalid_filters_are_rejected(client, query):
    assert client.get("/api/v1/matches", params=query).status_code == 422


def test_start_time_closes_betting_without_fabricating_live_status(db):
    now = datetime.now(timezone.utc)
    seed_demo(db, now)
    output = serialize_match(lookup(db, Match, "demo-w1"), (1, 1), now + timedelta(hours=2))
    assert output.bettingOpen is False and output.status == "scheduled"


def test_team_display_accepts_new_external_team_without_registration(db, client):
    season = Season(public_code="real", slug="real", year=2026, name="Real",
                    status="active", initial_points=2000)
    db.add(season)
    db.flush()
    row = make_match(db, id="real-1", season_id=season.id, api_source="provider",
        external_match_id="external-1", round="BO3", team_a_id="NEW_TEAM", team_b_id="OTHER_TEAM",
        starts_at=datetime.now(timezone.utc) + timedelta(days=1), status="scheduled", is_demo=False,
        api_data={"teams": [{"id": "NEW_TEAM", "name": "New Team"}, {"id": "OTHER_TEAM", "name": "Other Team"}],
                  "normalized": {"source_status": "not_started", "issues": []}})
    db.add(row)
    member = Member(auth_user_id=uuid4(), google_sub="google-test")
    db.add(member)
    db.flush()
    place_wager(db, member_id=member.id, season_id=season.id, match_id=row.id,
        selected_team_id="NEW_TEAM", amount=1000, idempotency_key="stake-request-1")
    db.commit()
    item = client.get("/api/v1/matches/real-1").json()
    assert item["pools"] == [1000, 0] and item["poolSource"] == "bets"
    assert item["teamDetails"][0]["name"] == "New Team"


def test_seed_refuses_real_season(db):
    db.add(
        Season(
            public_code="worlds-2026",
            slug="worlds",
            year=2026,
            name="Worlds",
            label="2026 Worlds",
            status="active",
            is_demo=False,
        )
    )
    db.commit()
    with pytest.raises(ValueError, match="real data"):
        seed_demo(db)
    db.rollback()
    assert lookup(db, Season, "worlds-2026").is_demo is False


def test_negative_member_points_are_rejected(db):
    db.add(Member(auth_user_id=uuid4(), google_sub="negative", current_points=-1))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_duplicate_bet_on_same_match_is_rejected(db):
    season = Season(public_code="s", slug="s", year=2026, name="S", status="active")
    member = Member(auth_user_id=uuid4(), google_sub="duplicate")
    db.add_all([season, member])
    db.flush()
    row = make_match(db, id="m", season_id=season.id, api_source="test", external_match_id="m",
        round="BO3", team_a_id="A", team_b_id="B", starts_at=datetime.now(timezone.utc), status="scheduled")
    db.add(row)
    db.flush()
    for key in ["one", "two"]:
        db.add(make_entry(db, member_id=member.id, season_id=season.id, match_id=row.id,
            selected_side=1, amount=-10, reason="bet_stake", balance_after=0, idempotency_key=key))
        if key == "one":
            db.flush()
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
