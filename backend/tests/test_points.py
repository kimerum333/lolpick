from compact_fixtures import internal, lookup, make_match, make_entry
from app.models import Season, Match, Member
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.models import PointTransaction, SystemLog
from app.services.points import PointOperationError, activate_season, apply_points, finish_season


@pytest.fixture
def member(db):
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
    db.flush()
    row = Member(auth_user_id=uuid4(), google_sub="test-google-sub")
    db.add(row)
    db.commit()
    return row


def reward(db, member, key="reward-1", amount=10, season_id="s1"):
    return apply_points(
        db,
        member_id=internal(db, Member, member.id),
        season_id=internal(db, Season, season_id),
        amount=amount,
        reason="reward",
        idempotency_key=key,
    )


def test_retry_does_not_duplicate_money_or_business_metrics(db, member):
    first = reward(db, member)
    second = reward(db, member)
    assert first.id == second.id and member.current_points == 110
    assert db.scalar(select(func.count()).select_from(PointTransaction)) == 2
    assert db.scalar(select(func.count()).select_from(SystemLog)) == 1
    with pytest.raises(PointOperationError, match="reused"):
        reward(db, member, amount=11)


def test_reset_preserves_old_history_and_is_idempotent(db, member):
    reward(db, member)
    db.commit()
    with db.begin():
        finish_season(db, "s1")
        assert activate_season(db, "s2") == 1
    assert member.current_season_id == internal(db, Season, "s2") and member.current_points == 20
    assert (
        db.scalar(
            select(func.sum(PointTransaction.amount)).where(
                PointTransaction.season_id == internal(db, Season, "s1"),
                PointTransaction.member_id == internal(db, Member, member.id),
            )
        )
        == 110
    )
    assert (
        db.scalar(
            select(func.sum(PointTransaction.amount)).where(
                PointTransaction.season_id == internal(db, Season, "s2"),
                PointTransaction.member_id == internal(db, Member, member.id),
            )
        )
        == 20
    )
    reward(db, member, key="new-season-reward", amount=5, season_id=internal(db, Season, "s2"))
    assert activate_season(db, "s2") == 0
    assert member.current_points == 25
    with pytest.raises(PointOperationError, match="active season"):
        reward(db, member, key="late-old-settlement", season_id=internal(db, Season, "s1"))


def test_zero_point_season_has_an_opening_record(db, member):
    lookup(db, Season, "s2").initial_points = 0
    finish_season(db, "s1")
    assert activate_season(db, "s2") == 1
    opening = db.scalar(select(PointTransaction).where(PointTransaction.season_id == internal(db, Season, "s2")))
    assert opening.reason == "season_open" and opening.amount == opening.balance_after == 0
    assert member.current_points == 0


def test_transaction_failure_rolls_back_balance_ledger_and_log(db, member):
    reward(db, member)
    db.commit()
    with pytest.raises(PointOperationError):
        with db.begin():
            reward(db, member, key="will-rollback", amount=50)
            apply_points(
                db,
                member_id=internal(db, Member, member.id),
                season_id=internal(db, Season, "s1"),
                amount=-1000,
                reason="admin_adjustment",
                idempotency_key="insufficient",
            )
    db.refresh(member)
    assert member.current_points == 110
    assert db.scalar(select(func.count()).select_from(PointTransaction)) == 2
    assert db.scalar(select(func.count()).select_from(SystemLog)) == 1


@pytest.mark.parametrize("status", ["scheduled", "live", "finished", "cancelled"])
def test_unsettled_or_unrefunded_matches_block_reset(db, member, status):
    db.add(
        make_match(
            db,
            id="m1",
            season_id="s1",
            api_source="test",
            external_match_id="m1",
            round="BO3",
            team_a_id="A",
            team_b_id="B",
            starts_at=datetime.now(timezone.utc),
            status=status,
        )
    )
    db.commit()
    with pytest.raises(PointOperationError, match="unfinished"):
        finish_season(db, "s1")
    db.rollback()
    assert lookup(db, Season, "s1").status == "active"
    assert lookup(db, Season, "s2").status == "upcoming"


def test_inconsistent_final_match_is_rejected_before_reset(db, member):
    now = datetime.now(timezone.utc)
    db.add(make_match(db, id="m1", season_id="s1", api_source="test",
        external_match_id="m1", round="BO3", team_a_id="A", team_b_id="B",
        starts_at=now, status="settled", settled_at=now,
        result="team_a_win", result_recorded_at=now, settlement_status="settled"))
    with pytest.raises(IntegrityError):
        db.flush()  # Final totals cannot be absent.
    db.rollback()


def test_bet_cannot_reference_a_match_in_another_season(db, member):
    db.add(
        make_match(
            db,
            id="m1",
            season_id="s1",
            api_source="test",
            external_match_id="m1",
            round="BO3",
            team_a_id="A",
            team_b_id="B",
            starts_at=datetime.now(timezone.utc),
            status="scheduled",
        )
    )
    db.flush()
    db.add(make_entry(db, member_id=member.id, season_id="s2", match_id="m1",
        selected_side=1, amount=-1, balance_after=99, reason="bet_stake",
        idempotency_key="wrong-season"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_multiple_seasons_can_be_active(db, member):
    lookup(db, Season, "s2").status = "active"
    db.flush()
    assert lookup(db, Season, "s1").status == lookup(db, Season, "s2").status == "active"


def test_match_point_operation_derives_season_and_rejects_nonparticipant_team(db, member):
    from app.services.points import apply_match_points

    db.add(
        make_match(
            db,
            id="m1",
            season_id="s1",
            api_source="test",
            external_match_id="m1",
            round="BO3",
            team_a_id="A",
            team_b_id="B",
            starts_at=datetime.now(timezone.utc) + __import__("datetime").timedelta(days=1),
            api_data={"normalized": {"source_status": "not_started", "issues": []}},
            status="scheduled",
        )
    )
    db.flush()
    with pytest.raises(PointOperationError):
        apply_match_points(db, member_id=member.id, match_id=internal(db, Match, "m1"),
            selected_side=3, amount=-10, reason="bet_stake", idempotency_key="invalid-side")
    entry = apply_match_points(db, member_id=member.id, match_id=internal(db, Match, "m1"),
        selected_side=1, amount=-10, reason="bet_stake", idempotency_key="stake")
    assert entry.season_id == internal(db, Season, "s1") and entry.match_id == internal(db, Match, "m1")
