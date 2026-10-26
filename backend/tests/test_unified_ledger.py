"""v7 service regressions. SQLite does not exercise PostgreSQL security guards."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from app.models import Base, Member, PointTransaction, Season, Wager
from app.db.compact import point_key_hash
from app.services.points import PointOperationError, apply_points
from app.services.settlement import process_result
from app.services.catalog import get_match
from app.services.teams import preserve_team
from compact_fixtures import make_match


@pytest.fixture
def game(db):
    season = Season(public_code="v7", slug="v7", year=2026, name="V7",
                    status="active", initial_points=100)
    db.add(season)
    db.flush()
    match = make_match(db, id="v7-match", season_id=season.id, api_source="test",
        external_match_id="v7-match", round="BO3", team_a_id="A", team_b_id="B",
        starts_at=datetime.now(timezone.utc) + timedelta(days=1), status="scheduled",
        api_data={"normalized": {"source_status": "not_started", "issues": []}})
    db.add(match)
    people = [Member(auth_user_id=uuid4(), google_sub=f"v7-{uuid4()}") for _ in range(3)]
    db.add_all(people)
    db.flush()
    return season, match, people


def place(db, game, index, amount, side, key="stake"):
    season, match, people = game
    return apply_points(db, member_id=people[index].id, season_id=season.id,
        match_id=match.id, selected_side=side, amount=-amount,
        reason="bet_stake", idempotency_key=key)


def test_stake_is_wager_and_retry_cannot_debit_twice(db, game):
    season, match, people = game
    first = place(db, game, 0, 10, 1)
    assert place(db, game, 0, 10, 1).id == first.id
    assert people[0].current_points == 90
    assert first.related_entry_id is None
    wager = db.get(Wager, first.id)
    assert wager.amount == 10 and wager.status == "pending"
    assert first.idempotency_key_hash == point_key_hash(people[0].id, season.id, "bet_stake", "stake")
    with pytest.raises(PointOperationError):
        place(db, game, 0, 11, 1)
    with pytest.raises(PointOperationError):
        place(db, game, 0, 10, 1, key="different")
    assert db.scalar(select(func.count()).select_from(PointTransaction)
                     .where(PointTransaction.reason == "bet_stake")) == 1


def test_same_request_key_is_scoped_to_member(db, game):
    one = place(db, game, 0, 10, 1)
    two = place(db, game, 1, 10, 2)
    assert one.idempotency_key_hash != two.idempotency_key_hash


@pytest.mark.parametrize("result", ["team_a_win", "draw", "cancelled"])
def test_atomic_settlement_and_read_only_projection(db, game, result):
    stakes = [place(db, game, i, amount, side) for i, (amount, side)
              in enumerate(((1, 1), (2, 1), (7, 2)))]
    db.commit()
    with db.begin():
        outcome = process_result(db, "v7-match", result)
    assert outcome["pool"] == 10
    assert outcome["paid"] == (9 if result == "team_a_win" else 10)
    assert [person.current_points for person in game[2]] == (
        [102, 104, 93] if result == "team_a_win" else [100, 100, 100])
    finals = list(db.scalars(select(PointTransaction).where(
        PointTransaction.reason.in_(["bet_payout", "bet_refund"]))))
    assert {entry.related_entry_id for entry in finals} <= {entry.id for entry in stakes}
    db.commit()
    with db.begin():
        assert process_result(db, "v7-match", result) == outcome
    assert db.scalar(select(func.count()).select_from(PointTransaction)) == 6 + len(finals)
    wager = db.get(Wager, stakes[0].id)
    wager.amount = 999
    with pytest.raises(ValueError, match="read-only"):
        db.flush()
    db.rollback()


def test_refund_cannot_reference_another_owner(db, game):
    stake = place(db, game, 0, 10, 1)
    from app.services.settlement import record_result
    record_result(game[1], "cancelled")
    db.flush()
    with pytest.raises(PointOperationError):
        apply_points(db, member_id=game[2][1].id, season_id=game[0].id,
            match_id=game[1].id, related_entry_id=stake.id, amount=10,
            reason="bet_refund", idempotency_key="forged")


def test_settlement_failure_rolls_back_result_and_all_credits(db, game, monkeypatch):
    for i, (amount, side) in enumerate(((1, 1), (2, 1), (7, 2))):
        place(db, game, i, amount, side)
    db.commit()
    from app.services import settlement
    real = settlement.apply_points
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PointOperationError("Injected second payment failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(settlement, "apply_points", fail_second)
    with pytest.raises(PointOperationError):
        with db.begin():
            process_result(db, "v7-match", "team_a_win")
    assert game[1].result is None
    assert [person.current_points for person in game[2]] == [99, 98, 93]
    assert db.scalar(select(func.count()).select_from(PointTransaction)
                     .where(PointTransaction.reason == "bet_payout")) == 0


def test_tbd_fixture_is_visible_and_not_bettable(db, game):
    game[1].team_a_id = game[1].team_b_id = None
    game[1].starts_at = None
    db.flush()
    db.expire(game[1])
    output = get_match(db, "v7-match")
    assert output.teams == (None, None)
    assert output.teamDetails == (None, None)
    assert output.startsAt is None and not output.bettingOpen
    with pytest.raises(PointOperationError):
        place(db, game, 0, 1, 1)


def test_team_display_updates_one_identity_and_rejects_stale_import(db):
    now = datetime.now(timezone.utc)
    first = preserve_team(db, "test", {"id": "rename", "name": "Old"}, now)
    identity = first.id
    later = preserve_team(db, "test", {"id": "rename", "name": "New"}, now + timedelta(hours=1))
    stale = preserve_team(db, "test", {"id": "rename", "name": "Old"}, now)
    assert identity == later.id == stale.id and stale.name == "New"


def test_nine_tables_with_type_and_without_separate_wagers():
    names = {table.name for table in Base.metadata.tables.values()}
    assert len(names) == 9
    assert {"lolpick_bets", "lolpick_admin_members", "lolpick_team_snapshots"}.isdisjoint(names)
    assert "type" in Member.__table__.c
    assert "related_entry_id" in PointTransaction.__table__.c
