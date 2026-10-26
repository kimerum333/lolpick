from compact_fixtures import internal, lookup, make_match, make_entry
from app.models import Season, Match, Member
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.models import Bet, PointTransaction, SystemLog
from app.services.point_reports import PointReportError, match_stake_ranking
from app.services.points import PointOperationError, activate_season, apply_points, finish_season


@pytest.fixture
def match_context(db):
    db.add_all(
        [
            Season(
                public_code="s1",
                slug="test",
                year=2026,
                name="Test",
                label="Test",
                status="active",
                initial_points=1000,
            ),
            Season(
                public_code="s2",
                slug="test",
                year=2027,
                name="Test",
                label="Test",
                status="upcoming",
                initial_points=500,
            ),
        ]
    )
    db.flush()
    for season_id, match_id in [("s1", "m1"), ("s2", "m2")]:
        db.add(
            make_match(
                db,
                id=match_id,
                season_id=season_id,
                api_source="test",
                external_match_id=match_id,
                round="BO3",
                team_a_id="A",
                team_b_id="B",
                starts_at=datetime.now(timezone.utc) + timedelta(days=1),
                api_data={"normalized": {"source_status": "not_started", "issues": []}},
                status="scheduled",
                is_demo=False,
            )
        )
    db.commit()


def raw_bet(db, *, amount=100, season_id="s1", match_id="m1"):
    """Only an owner/context; v7 has no unfunded wager row."""
    from types import SimpleNamespace
    member = Member(auth_user_id=uuid4(), google_sub=f"test-{uuid4()}")
    db.add(member)
    db.flush()
    return member, SimpleNamespace(id=None, selected_side=1)


def stake(db, amount=100, *, key=None, created_at=None):
    member, _ = raw_bet(db, amount=amount)
    entry = apply_points(db, member_id=member.id, season_id=internal(db, Season, "s1"),
        match_id=internal(db, Match, "m1"), selected_side=1, amount=-amount,
        reason="bet_stake", idempotency_key=key or str(uuid4()))
    if created_at:
        entry.created_at = created_at  # Explicit SQLite fixture timestamp only.
        db.flush()
    return member, db.get(Bet, entry.id), entry


def test_stake_records_season_match_and_bet_while_opening_has_no_match(db, match_context):
    member, bet, entry = stake(db)
    assert (entry.season_id, entry.match_id, entry.id) == (
        internal(db, Season, "s1"),
        internal(db, Match, "m1"),
        bet.id,
    )
    assert member.current_points == 900
    opening = db.scalar(
        select(PointTransaction).where(
            PointTransaction.member_id == internal(db, Member, member.id), PointTransaction.reason == "season_open"
        )
    )
    assert (
        opening.season_id == internal(db, Season, "s1")
        and opening.match_id is internal(db, Match, None)
        and (opening.related_entry_id is None)
    )


def test_same_bet_with_another_request_key_cannot_debit_twice(db, match_context):
    member, bet, entry = stake(db, key="first-stake")
    retry = apply_points(
        db,
        member_id=internal(db, Member, member.id),
        season_id=internal(db, Season, "s1"),
        match_id=internal(db, Match, "m1"),
        selected_side=1,
        amount=-100,
        reason="bet_stake",
        idempotency_key="first-stake",
    )
    assert retry.id == entry.id
    with pytest.raises(PointOperationError, match="already wagered"):
        apply_points(
            db,
            member_id=internal(db, Member, member.id),
            season_id=internal(db, Season, "s1"),
            match_id=internal(db, Match, "m1"),
            selected_side=1,
            amount=-100,
            reason="bet_stake",
            idempotency_key="second-stake",
        )
    assert member.current_points == 900
    assert (
        db.scalar(
            select(func.count())
            .select_from(PointTransaction)
            .where(PointTransaction.id == bet.id, PointTransaction.reason == "bet_stake")
        )
        == 1
    )


@pytest.mark.parametrize("reason,amount", [("bet_stake", 99), ("bet_refund", 99)])
def test_bet_amount_must_match_recorded_stake_before_balance_changes(db, match_context, reason, amount):
    if reason == "bet_refund":
        member, bet, _ = stake(db)
        from app.services.settlement import record_result
        record_result(lookup(db, Match, "m1"), "cancelled")
        db.flush()
        previous = member.current_points
        params = {"related_entry_id": bet.id}
    else:
        member, _ = raw_bet(db)
        previous = member.current_points
        params = {"selected_side": 1}
    with pytest.raises(PointOperationError):
        apply_points(db, member_id=member.id, season_id=internal(db, Season, "s1"),
            match_id=internal(db, Match, "m1"), amount=amount, reason=reason,
            idempotency_key="wrong-amount", **params)
    assert member.current_points == previous


def test_payout_and_refund_cannot_both_credit_one_bet(db, match_context):
    member, bet, _ = stake(db)
    from app.services.settlement import record_result
    record_result(lookup(db, Match, "m1"), "team_a_win")
    db.flush()
    apply_points(
        db,
        member_id=internal(db, Member, member.id),
        season_id=internal(db, Season, "s1"),
        match_id=internal(db, Match, "m1"),
        related_entry_id=bet.id,
        amount=100,
        reason="bet_payout",
        idempotency_key="pay",
    )
    with pytest.raises(PointOperationError, match="both a payout and a refund"):
        apply_points(
            db,
            member_id=internal(db, Member, member.id),
            season_id=internal(db, Season, "s1"),
            match_id=internal(db, Match, "m1"),
            related_entry_id=bet.id,
            amount=100,
            reason="bet_refund",
            idempotency_key="refund-after-pay",
        )
    assert member.current_points == 1000


@pytest.mark.parametrize("reason", ["bet_payout", "bet_refund"])
def test_credit_without_a_stake_is_rejected(db, match_context, reason):
    member, bet = raw_bet(db)
    with pytest.raises(PointOperationError):
        apply_points(
            db,
            member_id=internal(db, Member, member.id),
            season_id=internal(db, Season, "s1"),
            match_id=internal(db, Match, "m1"),
            related_entry_id=999999,
            amount=100,
            reason=reason,
            idempotency_key="no-stake",
        )
    assert member.current_season_id is internal(db, Season, None)


@pytest.mark.parametrize(
    "reason,amount,with_bet,with_match",
    [
        ("bet_stake", -100, False, True),
        ("bet_stake", -100, True, False),
        ("bet_stake", 100, True, True),
        ("bet_payout", -100, True, True),
        ("bet_refund", -100, True, True),
        ("reward", 100, True, True),
        ("season_open", 1000, False, True),
        ("attendance", -100, False, False),
    ],
)
def test_database_rejects_direct_inserts_with_missing_context_or_wrong_direction(
    db, match_context, reason, amount, with_bet, with_match
):
    member, bet = raw_bet(db)
    db.add(
        make_entry(
            db,
            id=uuid4(),
            member_id=member.id,
            season_id="s1",
            match_id="m1" if with_match else None,
            selected_side=1 if with_bet else None,
            reason=reason,
            amount=amount,
            balance_after=1000,
            idempotency_key=str(uuid4()),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_database_unique_bet_reason_rejects_bypassing_service(db, match_context):
    member, bet, _ = stake(db)
    db.add(
        make_entry(
            db,
            id=uuid4(),
            member_id=member.id,
            season_id="s1",
            match_id="m1",
            selected_side=1,
            reason="bet_stake",
            amount=-100,
            balance_after=800,
            idempotency_key="direct-double-debit",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_database_rejects_both_final_postings_even_if_service_is_bypassed(db, match_context):
    member, bet, _ = stake(db)
    for reason, amount in [("bet_payout", 150), ("bet_refund", 100)]:
        db.add(
            make_entry(
                db,
                id=uuid4(),
                member_id=member.id,
                season_id="s1",
                match_id="m1",
                related_entry_id=bet.id,
                reason=reason,
                amount=amount,
                balance_after=1000,
                idempotency_key=str(uuid4()),
            )
        )
        if reason == "bet_payout":
            db.flush()
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_database_rejects_ledger_match_in_another_season(db, match_context):
    member, _ = raw_bet(db)
    db.add(
        make_entry(
            db,
            id=uuid4(),
            member_id=member.id,
            season_id="s1",
            match_id="m2",
            reason="reward",
            amount=10,
            balance_after=10,
            idempotency_key="cross-season",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_match_ranking_uses_stakes_not_rewards_payouts_or_current_balance(db, match_context):
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    first, first_bet, _ = stake(db, 100, created_at=now)
    second, second_bet, _ = stake(db, 300, created_at=now + timedelta(seconds=1))
    third, _, _ = stake(db, 300, created_at=now + timedelta(seconds=2))
    from app.services.settlement import record_result
    record_result(lookup(db, Match, "m1"), "team_a_win")
    db.flush()
    apply_points(
        db,
        member_id=internal(db, Member, first.id),
        season_id=internal(db, Season, "s1"),
        match_id=internal(db, Match, "m1"),
        related_entry_id=first_bet.id,
        amount=500,
        reason="bet_payout",
        idempotency_key="large-pay",
    )
    apply_points(
        db,
        member_id=internal(db, Member, second.id),
        season_id=internal(db, Season, "s1"),
        match_id=internal(db, Match, "m1"),
        amount=50,
        reason="reward",
        idempotency_key="match-reward",
    )
    result = match_stake_ranking(db, season_id="s1", match_id="m1")
    assert [row["stakePoints"] for row in result["items"]] == [300, 300, 100]
    assert [row["amountRank"] for row in result["items"]] == [1, 1, 3]
    assert [row["entryOrder"] for row in result["items"]] == [2, 3, 1]
    assert [row["memberId"] for row in result["items"]] == [str(second.id), str(third.id), str(first.id)]
    assert result["total"] == 3 and result["unfundedBets"] == 0
    assert result["items"][0]["teamId"] == "A"
    assert "google_sub" not in str(result) and "current_points" not in str(result)


def test_rank_is_computed_before_pagination_and_time_order_is_selectable(db, match_context):
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    for index, amount in enumerate([100, 300, 200]):
        stake(db, amount, created_at=now + timedelta(seconds=index))
    page = match_stake_ranking(db, season_id="s1", match_id="m1", limit=1, offset=1)
    assert page["total"] == 3 and page["items"][0]["amountRank"] == 2
    time_page = match_stake_ranking(db, season_id="s1", match_id="m1", order_by="time")
    assert [row["stakePoints"] for row in time_page["items"]] == [100, 300, 200]


def test_refund_ledger_excludes_bet_even_before_status_flag_updates(db, match_context):
    member, bet, _ = stake(db)
    from app.services.settlement import record_result
    record_result(lookup(db, Match, "m1"), "cancelled")
    db.flush()
    apply_points(
        db,
        member_id=internal(db, Member, member.id),
        season_id=internal(db, Season, "s1"),
        match_id=internal(db, Match, "m1"),
        related_entry_id=bet.id,
        amount=100,
        reason="bet_refund",
        idempotency_key="refund",
    )
    assert bet.status == "pending"
    assert match_stake_ranking(db, season_id="s1", match_id="m1")["total"] == 0
    historical = match_stake_ranking(db, season_id="s1", match_id="m1", include_refunded=True)
    assert historical["total"] == 1 and historical["items"][0]["refunded"]
    assert member.current_points == 1000


def test_unfunded_bet_is_reported_without_inventing_a_rank(db, match_context):
    raw_bet(db)
    result = match_stake_ranking(db, season_id="s1", match_id="m1")
    assert result["items"] == [] and result["total"] == 0 and (result["unfundedBets"] == 0)


def test_public_pool_counts_only_debited_stakes_and_excludes_refund_ledger(client, db, match_context):
    db.commit()
    assert client.get("/api/v1/matches/m1").json()["pools"] == [0, 0]
    member, bet, _ = stake(db)
    db.commit()
    assert client.get("/api/v1/matches/m1").json()["pools"] == [100, 0]
    from app.services.settlement import record_result
    record_result(lookup(db, Match, "m1"), "cancelled")
    db.flush()
    apply_points(db, member_id=member.id, season_id=internal(db, Season, "s1"),
        match_id=internal(db, Match, "m1"), related_entry_id=bet.id,
        amount=100, reason="bet_refund", idempotency_key="pool-refund")
    db.commit()
    assert client.get("/api/v1/matches/m1").json()["pools"] == [0, 0]


def test_amount_tampering_is_rejected_on_read_only_wager(db, match_context):
    _, bet, _ = stake(db)
    bet.amount = 101
    with pytest.raises(ValueError, match="read-only"):
        db.flush()
    db.rollback()


def test_season_rollover_preserves_match_context_and_historical_ranking(db, match_context):
    member, _, entry = stake(db)
    from app.services.settlement import process_result
    process_result(db, "m1", "team_a_win")
    db.commit()
    with db.begin():
        finish_season(db, "s1")
        activate_season(db, "s2")
    assert member.current_points == 500 and member.current_season_id == internal(db, Season, "s2")
    db.refresh(entry)
    assert entry.season_id == internal(db, Season, "s1") and entry.match_id == internal(db, Match, "m1")
    assert entry.amount == -100
    assert match_stake_ranking(db, season_id="s1", match_id="m1")["items"][0]["stakePoints"] == 100


@pytest.mark.parametrize(
    "kwargs",
    [
        {"season_id": "s2"},
        {"match_id": "missing"},
        {"limit": 101},
        {"limit": True},
        {"offset": -1},
        {"order_by": "balance"},
    ],
)
def test_report_rejects_wrong_scope_and_invalid_filters(db, match_context, kwargs):
    with pytest.raises(PointReportError):
        match_stake_ranking(db, **{"season_id": "s1", "match_id": "m1", **kwargs})


def test_reporting_is_read_only_and_same_timestamp_order_is_stable(db, match_context):
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    _, _, first = stake(db, created_at=now)
    _, _, second = stake(db, created_at=now)
    assert first.id < second.id
    db.commit()
    log_count = db.scalar(select(func.count()).select_from(SystemLog))
    point_count = db.scalar(select(func.count()).select_from(PointTransaction))
    result = match_stake_ranking(db, season_id="s1", match_id="m1", order_by="time")
    assert [row["transactionId"] for row in result["items"]] == [str(first.id), str(second.id)]
    assert db.scalar(select(func.count()).select_from(SystemLog)) == log_count
    assert db.scalar(select(func.count()).select_from(PointTransaction)) == point_count
