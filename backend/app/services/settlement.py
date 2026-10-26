"""Atomic settlement: stake entries are the wagers; finals reference their IDs."""

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.schema import MAX_POINTS
from app.models import Match, Member, PointTransaction, Season
from app.services.activity import record_event
from app.services.member_locks import lock_member_rows
from app.services.points import PointOperationError, apply_points, verify_member_ledger

RESULTS = {"team_a_win", "team_b_win", "draw", "cancelled"}


def locked_match(db: Session, match_id: str) -> tuple[Match, Season]:
    if not isinstance(match_id, str) or not 1 <= len(match_id) <= 80:
        raise PointOperationError("Invalid match identifier.")
    identity = db.execute(select(Match.id, Match.season_id).where(Match.public_code == match_id)).first()
    if identity is None:
        raise PointOperationError("Match does not exist.")
    season = db.scalar(select(Season).where(Season.id == identity.season_id).with_for_update().execution_options(populate_existing=True))
    match = db.scalar(select(Match).where(Match.id == identity.id).with_for_update()
                      .execution_options(populate_existing=True))
    if season is None or season.status != "active":
        raise PointOperationError("Results can only be processed in the active season.")
    if season.is_demo or match.is_demo:
        raise PointOperationError("Demo matches do not change real points.")
    return match, season


def record_result(match: Match, result: str, *, score_a=None, score_b=None, recorded_at=None) -> None:
    if not isinstance(result, str) or result not in RESULTS:
        raise PointOperationError("Invalid match result.")
    if (score_a is None) != (score_b is None) or any(
        value is not None and (type(value) is not int or not 0 <= value <= 32_767)
        for value in (score_a, score_b)
    ):
        raise PointOperationError("Invalid match scores.")
    if result != "cancelled" and (match.team_a_id is None or match.team_b_id is None):
        raise PointOperationError("A confirmed result requires both teams.")
    if match.settlement_status in {"settled", "refunded"}:
        if match.result != result or (score_a is not None and (match.score_a, match.score_b) != (score_a, score_b)):
            raise PointOperationError("A finalized result cannot be changed.")
        return
    match.result = result
    match.result_recorded_at = recorded_at or datetime.now(timezone.utc)
    match.status = "cancelled" if result == "cancelled" else "finished"
    match.score_a, match.score_b = score_a, score_b


def summary(match: Match) -> dict:
    return {
        "matchId": match.public_code,
        "seasonId": match.season_row.public_code,
        "result": match.result,
        "winner": (
            match.team_a.external_team_id
            if match.result == "team_a_win"
            else match.team_b.external_team_id
            if match.result == "team_b_win"
            else None
        ),
        "settlementStatus": match.settlement_status,
        "pool": match.settlement_pool,
        "paid": match.settlement_paid,
        "removedRemainder": match.settlement_remainder,
    }


def settle_locked_match(db: Session, match: Match, season: Season, *, actor_member_id=None, request_id=None) -> dict:
    """Internal; caller must already hold exclusive season and match locks."""
    if season.status != "active" or season.id != match.season_id:
        raise PointOperationError("Settlement requires the active match season.")
    if match.is_demo or season.is_demo:
        raise PointOperationError("Demo matches do not change real points.")
    if match.settlement_status in {"settled", "refunded"}:
        return summary(match)
    if match.result not in RESULTS:
        raise PointOperationError("Record a confirmed result before settlement.")
    if match.issue_flags & 4064:
        raise PointOperationError("Resolve provider conflicts before settlement.")
    entries = list(db.scalars(
        select(PointTransaction).where(
            PointTransaction.match_id == match.id,
            PointTransaction.reason.in_(["bet_stake", "bet_payout", "bet_refund"]),
        ).order_by(PointTransaction.member_id, PointTransaction.id)
    ))
    if any(entry.reason != "bet_stake" for entry in entries):
        raise PointOperationError("Partial settlement requires investigation.")
    stakes = entries
    members = {row.id: row for row in lock_member_rows(db, [entry.member_id for entry in stakes])}
    for entry in stakes:
        member = members.get(entry.member_id)
        if (entry.season_id != season.id or entry.amount >= 0
                or entry.selected_side not in (1, 2)
                or member is None):
            raise PointOperationError("Stake and owner do not agree.")
    for member in members.values():
        verify_member_ledger(db, member, season.id)
    pool = sum(-entry.amount for entry in stakes)
    if pool > MAX_POINTS:
        raise PointOperationError("Match pool exceeds the safe point limit.")
    winner_side = 1 if match.result == "team_a_win" else 2 if match.result == "team_b_win" else None
    winning_pool = sum(-entry.amount for entry in stakes if entry.selected_side == winner_side)
    refund = match.result in {"draw", "cancelled"} or winning_pool == 0
    if not refund and match.issue_flags:
        raise PointOperationError("Resolve provider issues before paying winnings.")
    payouts = {
        entry.id: -entry.amount if refund else
        (-entry.amount * pool // winning_pool if entry.selected_side == winner_side else 0)
        for entry in stakes
    }
    for member_id, member in members.items():
        credit = sum(payouts[entry.id] for entry in stakes if entry.member_id == member_id)
        if verify_member_ledger(db, member, season.id) + credit > MAX_POINTS:
            raise PointOperationError("Settlement exceeds a member's point limit.")
    now = datetime.now(timezone.utc)
    for entry in stakes:
        payout = payouts[entry.id]
        if payout:
            reason = "bet_refund" if refund else "bet_payout"
            apply_points(
                db, member_id=entry.member_id, season_id=season.id,
                match_id=match.id, related_entry_id=entry.id, amount=payout,
                reason=reason, idempotency_key=f"settlement:{entry.id}:{reason}",
                request_id=request_id,
            )
    match.settlement_status = "refunded" if refund else "settled"
    match.settlement_pool = pool
    match.settlement_paid = sum(payouts.values())
    match.settlement_at = now
    db.flush()
    record_event(
        db,
        "bet.refunded" if refund else "bet.settled",
        season_id=season.id,
        actor_member_id=actor_member_id,
        request_id=request_id,
        event_key=f"match:{match.id}:settlement",
        context={
            "match_id": match.id,
            "count": len(stakes),
            "pool": pool,
            "paid": match.settlement_paid,
            "removed_remainder": match.settlement_remainder,
        },
    )
    return summary(match)


def process_result(
    db: Session, match_id: str, result: str, *, score_a=None, score_b=None, actor_member_id=None, request_id=None
) -> dict:
    match, season = locked_match(db, match_id)
    record_result(match, result, score_a=score_a, score_b=score_b)
    db.flush()
    return settle_locked_match(db, match, season, actor_member_id=actor_member_id, request_id=request_id)
