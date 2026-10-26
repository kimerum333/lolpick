"""v7 atomic points. Caller owns begin/commit/rollback.

Production PostgreSQL writes ONLY through lolpick.post_points. Never additionally
update members.current_points in the app. SQLite fallback is isolated unit-test
support and does not replace native trigger/RLS tests.
"""
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from app.db.schema import MAX_POINTS
from app.models import Wager, Match, Member, PointTransaction, Season, CheerContract
from app.services.activity import record_event
from app.services.member_locks import lock_member_rows
from app.db.compact import point_key_hash, code_for

REASONS = {"signup", "attendance", "reward", "bet_stake", "bet_payout", "bet_refund", "admin_adjustment"}

class PointOperationError(ValueError):
    CODES = {
        "Ledger total does not match the member balance.": "ledger_balance_mismatch",
        "Member must have exactly one season opening record.": "ledger_opening_mismatch",
        "Stake history is incomplete or contains partial settlement.": "stake_history_incomplete",
        "Bet, member and funded stake do not agree.": "stake_owner_mismatch",
        "Insufficient points or point limit exceeded.": "point_balance_limit",
        "Settlement would exceed a member's point limit.": "settlement_balance_limit",
        "Match pool exceeds the safe point limit.": "settlement_pool_limit",
        "Record the bet stake before its payout or refund.": "stake_missing",
        "Bet stake/refund amount must match the recorded bet amount.": "stake_amount_mismatch",
        "A bet cannot receive both a payout and a refund.": "settlement_conflict",
        "A finalized result cannot be changed.": "finalized_result_conflict",
        "Points can only change in the active season.": "season_not_active",
        "Results can only be processed in the active season.": "season_not_active",
        "Settlement requires the active match season.": "season_not_active",
        "Match is not eligible for betting.": "betting_closed",
        "Provider data is stale.": "provider_data_stale",
        "This member already wagered on this match.": "bet_already_placed",
        "Request key was reused for a different point operation.": "idempotency_conflict",
    }

    def __init__(self, message):
        super().__init__(message)
        self.code = self.CODES.get(message, "point_operation_rejected")



def verify_member_ledger(db: Session, member: Member, season_id: int | None = None) -> int:
    """Validate a season ledger; the member columns cache only the last touched season."""
    selected = season_id if season_id is not None else member.current_season_id
    if selected is None:
        if member.current_points != 0:
            raise PointOperationError("Ledger total does not match the member balance.")
        return 0
    filters = (PointTransaction.member_id == member.id, PointTransaction.season_id == selected)
    total = db.scalar(select(func.coalesce(func.sum(PointTransaction.amount), 0)).where(*filters))
    latest = db.scalar(select(PointTransaction).where(*filters).order_by(PointTransaction.id.desc()).limit(1))
    openings = db.scalar(select(func.count()).select_from(PointTransaction).where(
        *filters, PointTransaction.reason == "season_open"))
    if latest is None:
        if member.current_season_id == selected:
            raise PointOperationError("Member must have exactly one season opening record.")
        return 0
    if openings != 1:
        raise PointOperationError("Member must have exactly one season opening record.")
    if (not 0 <= total <= MAX_POINTS or latest.balance_after != total
            or (member.current_season_id == selected and total != member.current_points)):
        raise PointOperationError("Ledger total does not match the member balance.")
    return int(total)


def season_balance(db: Session, member: Member, season: Season) -> int:
    balance = verify_member_ledger(db, member, season.id)
    opened = db.scalar(select(PointTransaction.id).where(PointTransaction.member_id == member.id,
        PointTransaction.season_id == season.id, PointTransaction.reason == "season_open"))
    return balance if opened is not None else (season.initial_points if season.status == "active" and not season.is_demo else 0)


def locked_active_season(db: Session, season_id: int) -> Season:
    season = db.scalar(select(Season).where(Season.id == season_id).with_for_update(read=True).execution_options(populate_existing=True))
    if season is None or season.status != "active" or season.is_demo:
        raise PointOperationError("Points can only change in the active season.")
    return season


def _post(db, member, season, *, amount, reason, digest, match_id=None,
          related_entry_id=None, selected_side=None, contract_id=None, cheer_action=None):
    if db.get_bind().dialect.name == "postgresql":
        # One plain INSERT inside the function. No ORM ledger INSERT, UPSERT,
        # current_points assignment or implicit commit.
        db.flush()
        try:
            entry_id = db.scalar(text(
                "SELECT id FROM lolpick.post_points(:member,:season,CAST(:reason AS smallint),"
                ":amount,:key,:match,:related,CAST(:side AS smallint),:contract,CAST(:cheer AS smallint))"
            ), {"member": member.id, "season": season.id, "reason": code_for("point_reason", reason),
                "amount": amount, "key": digest, "match": match_id, "related": related_entry_id,
                "side": selected_side, "contract": contract_id,
                "cheer": code_for("cheer_action", cheer_action) if cheer_action is not None else None})
        except DBAPIError as exc:
            state = getattr(exc.orig, "sqlstate", None)
            if state in {"P0001", "23514", "23505", "23503", "23502"}:
                # Caller/nested transaction must roll back before any further SQL.
                raise PointOperationError("Database rejected the point operation; roll back this transaction.") from None
            raise
        db.expire(member, ["current_points", "current_season_id", "current_season", "updated_at"])
        return db.scalar(select(PointTransaction).where(PointTransaction.id == entry_id)
                         .execution_options(populate_existing=True))
    if db.get_bind().dialect.name != "sqlite":
        raise RuntimeError("Unsupported point-write database.")
    # Test database only: production settings require PostgreSQL.
    balance = amount if reason == "season_open" else verify_member_ledger(db, member, season.id) + amount
    if not 0 <= balance <= MAX_POINTS:
        raise PointOperationError("Insufficient points or point limit exceeded.")
    member.current_points = balance
    member.current_season_id = season.id
    member.current_season = season
    member.updated_at = datetime.now(timezone.utc)
    entry = PointTransaction(member_id=member.id, season_id=season.id, match_id=match_id,
        reason=reason, amount=amount, balance_after=balance, related_entry_id=related_entry_id,
        selected_side=selected_side, contract_id=contract_id, cheer_action=cheer_action,
        idempotency_key_hash=digest, created_at=datetime.now(timezone.utc))
    db.add(entry)
    db.flush()
    return entry


def open_member_season(db: Session, member: Member, season: Season) -> PointTransaction:
    season = locked_active_season(db, season.id)
    member = lock_member_rows(db, [member.id])[0]
    key = f"season:{season.id}:member:{member.id}:open"
    digest = point_key_hash(member.id, season.id, "season_open", key)
    existing = db.scalar(select(PointTransaction).where(PointTransaction.idempotency_key_hash == digest))
    if existing is not None:
        raise PointOperationError("This member already has an opening record for the target season.")
    verify_member_ledger(db, member)
    if season.status != "active" or season.is_demo:
        raise PointOperationError("Invalid season opening.")
    if db.scalar(select(PointTransaction.id).where(PointTransaction.member_id == member.id,
            PointTransaction.season_id == season.id).limit(1)) is not None:
        raise PointOperationError("Member must have exactly one season opening record.")
    return _post(db, member, season, amount=season.initial_points, reason="season_open", digest=digest)


def apply_points(db: Session, *, member_id: int, season_id: int, amount: int, reason: str,
                 idempotency_key: str, match_id: int | None = None,
                 related_entry_id: int | None = None, selected_side: int | None = None,
                 contract_id: int | None = None, cheer_action: str | None = None,
                 request_id: UUID | None = None) -> PointTransaction:
    if type(amount) is not int or amount == 0 or abs(amount) > MAX_POINTS:
        raise PointOperationError("amount must be a nonzero safe integer.")
    if not isinstance(reason, str) or reason not in REASONS or not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200 or idempotency_key != idempotency_key.strip():
        raise PointOperationError("Invalid point reason or request key.")
    if any(type(v) is not int or v <= 0 for v in (member_id, season_id)) or any(
        v is not None and (type(v) is not int or v <= 0) for v in (match_id,related_entry_id,contract_id)):
        raise PointOperationError("Invalid point owner, season or match identifier.")
    if request_id is not None and not isinstance(request_id, UUID):
        raise PointOperationError("Invalid request identifier.")
    if reason == "bet_stake":
        if amount >= 0 or match_id is None or type(selected_side) is not int or selected_side not in (1,2) or related_entry_id is not None:
            raise PointOperationError("A wager requires a negative stake, match and selected side.")
    elif reason in {"bet_payout", "bet_refund"}:
        if amount <= 0 or match_id is None or related_entry_id is None or selected_side is not None:
            raise PointOperationError("Final posts require a match and original stake.")
    elif related_entry_id is not None or selected_side is not None:
        raise PointOperationError("Only wager operations carry selection/reference fields.")
    if reason in {"signup", "attendance", "reward"} and amount <= 0:
        raise PointOperationError("This point reason must add points.")
    if (contract_id is None) != (cheer_action is None) or (contract_id is not None and
        (reason != "bet_stake" or cheer_action not in {"cheering","betrayal"})):
        raise PointOperationError("Invalid cheer contract/action.")
    # Lock order is always season -> match -> member -> contract.
    season = db.scalar(select(Season).where(Season.id == season_id).with_for_update(read=True).execution_options(populate_existing=True))
    if season is None:
        raise PointOperationError("Season does not exist.")
    match = None
    if match_id is not None:
        match = db.scalar(select(Match).where(Match.id == match_id).with_for_update().execution_options(populate_existing=True))
        if match is None or match.season_id != season_id or match.is_demo:
            raise PointOperationError("Match belongs to a different season or is a demo.")
    if db.scalar(select(Member.id).where(Member.id == member_id)) is None:
        raise PointOperationError("Member does not exist.")
    member = lock_member_rows(db, [member_id])[0]
    if member is None:
        raise PointOperationError("Member does not exist.")
    verify_member_ledger(db, member)
    digest = point_key_hash(member_id, season_id, reason, idempotency_key)
    existing = db.scalar(select(PointTransaction).where(PointTransaction.idempotency_key_hash == digest))
    if existing is not None:
        if (existing.member_id,existing.season_id,existing.amount,existing.reason,existing.match_id,
            existing.related_entry_id,existing.selected_side,existing.contract_id,existing.cheer_action) != (
            member_id,season_id,amount,reason,match_id,related_entry_id,selected_side,contract_id,cheer_action):
            raise PointOperationError("Request key was reused for a different point operation.")
        return existing
    if season.status != "active" or season.is_demo:
        raise PointOperationError("Points can only change in the active season.")
    if reason == "bet_stake":
        from app.services.betting_rules import betting_closed_reason
        closed = betting_closed_reason(match)
        if closed == "provider_data_stale":
            raise PointOperationError("Provider data is stale.")
        if closed is not None:
            raise PointOperationError("Match is not eligible for betting.")
        if db.scalar(select(PointTransaction.id).where(PointTransaction.member_id == member_id,
            PointTransaction.match_id == match_id,PointTransaction.reason == "bet_stake")) is not None:
            raise PointOperationError("This member already wagered on this match.")
        if contract_id is not None:
            contract = db.scalar(select(CheerContract).where(CheerContract.id == contract_id).with_for_update().execution_options(populate_existing=True))
            chosen = match.team_a_id if selected_side == 1 else match.team_b_id
            if contract is None or (contract.member_id,contract.season_id) != (member_id,season_id) or contract.finalized_at is not None:
                raise PointOperationError("Invalid cheer contract.")
            if cheer_action != ("cheering" if contract.team_id == chosen else "betrayal"):
                raise PointOperationError("Cheer action differs from selected team.")
    elif reason in {"bet_payout","bet_refund"}:
        stake = db.get(PointTransaction, related_entry_id)
        if stake is None or stake.reason != "bet_stake" or (
            stake.member_id,stake.season_id,stake.match_id) != (member_id,season_id,match_id):
            raise PointOperationError("Original stake does not belong to this member, season and match.")
        if db.scalar(select(PointTransaction.id).where(PointTransaction.related_entry_id == related_entry_id,
            PointTransaction.reason.in_(["bet_payout","bet_refund"]))) is not None:
            raise PointOperationError("A bet cannot receive both a payout and a refund.")
        if match.settlement_status != "pending" or match.result is None or (match.issue_flags & 4064):
            raise PointOperationError("Match is not eligible for settlement.")
        if reason == "bet_refund":
            if amount != -stake.amount:
                raise PointOperationError("Bet stake/refund amount must match the recorded bet amount.")
            if match.result in {"team_a_win","team_b_win"} and db.scalar(select(PointTransaction.id).where(
                PointTransaction.match_id == match_id,PointTransaction.reason == "bet_stake",
                PointTransaction.selected_side == (1 if match.result == "team_a_win" else 2)).limit(1)):
                raise PointOperationError("Winning wagers exist; use payout settlement.")
        else:
            side = 1 if match.result == "team_a_win" else 2 if match.result == "team_b_win" else None
            if match.status != "finished" or stake.selected_side != side or match.issue_flags:
                raise PointOperationError("Payout requires an unambiguous winning wager.")
            pool = db.scalar(select(func.coalesce(func.sum(-PointTransaction.amount),0)).where(
                PointTransaction.match_id == match_id,PointTransaction.reason == "bet_stake"))
            paid = db.scalar(select(func.coalesce(func.sum(PointTransaction.amount),0)).where(
                PointTransaction.match_id == match_id,PointTransaction.reason == "bet_payout"))
            if paid + amount > pool:
                raise PointOperationError("Payouts exceed the funded pool.")
    available = season_balance(db, member, season)
    if not 0 <= available + amount <= MAX_POINTS:
        raise PointOperationError("Insufficient points or point limit exceeded.")
    if db.scalar(select(PointTransaction.id).where(PointTransaction.member_id == member.id,
            PointTransaction.season_id == season.id, PointTransaction.reason == "season_open")) is None:
        open_member_season(db, member, season)
    entry = _post(db, member, season, amount=amount, reason=reason, digest=digest,
        match_id=match_id, related_entry_id=related_entry_id, selected_side=selected_side,
        contract_id=contract_id, cheer_action=cheer_action)
    record_event(db, "points.changed", season_id=season_id, actor_member_id=member_id,
        request_id=request_id, event_key=f"point:{entry.id}",
        context={"entry_id":entry.id, **({"match_id":match_id} if match_id else {})})
    return entry


def activate_season(db: Session, season_id: str, *, actor_member_id=None, request_id=None) -> int:
    from app.services.season_locks import membership_gate
    membership_gate(db, exclusive=True)
    # Lock all season rows in deterministic order. The command is infrequent.
    # All point writes take a shared lock on their season before locking members.
    target = db.scalar(select(Season).where(Season.public_code == season_id).with_for_update().execution_options(populate_existing=True))
    if target is None:
        raise PointOperationError("Target season does not exist.")
    if target.status == "active":
        return 0  # Repeated invocation cannot reset balances again.
    if target.status != "upcoming":
        raise PointOperationError("Only an upcoming season can be activated.")
    if db.scalar(select(func.count()).select_from(PointTransaction).where(PointTransaction.season_id == target.id)):
        raise PointOperationError("Upcoming season already contains point history.")
    members = lock_member_rows(db, db.scalars(select(Member.id).order_by(Member.id)))
    target.status = "active"
    db.flush()
    if not target.is_demo:
        for member in members:
            open_member_season(db, member, target)
    # Results collected while upcoming gain financial effect only after activation.
    from app.services.settlement import settle_locked_match

    if not target.is_demo:
        for match in db.scalars(
            select(Match)
            .where(
                Match.season_id == target.id,
                Match.result.is_not(None),
                Match.settlement_status == "pending",
                Match.is_demo.is_(False),
            )
            .order_by(Match.id)
            .with_for_update().execution_options(populate_existing=True)
        ):
            normalized = match.api_data.get("normalized", {}) if isinstance(match.api_data, dict) else {}
            issues = normalized.get("issues", []) if isinstance(normalized, dict) else ["invalid_provider_data"]
            if isinstance(issues, list) and not set(issues) - {"draw", "finished_without_winner"}:
                settle_locked_match(db, match, target, actor_member_id=actor_member_id, request_id=request_id)
    record_event(
        db,
        "season.activated",
        season_id=target.id,
        actor_member_id=actor_member_id,
        request_id=request_id,
        event_key=f"season:{target.id}:activated",
        context={"count": len(members)},
    )
    db.flush()
    return len(members)


def finish_season(db: Session, season_id: str, *, actor_member_id=None, request_id=None) -> None:
    target = db.scalar(select(Season).where(Season.public_code == season_id).with_for_update().execution_options(populate_existing=True))
    if target is None or target.status not in {"active", "finished"}:
        raise PointOperationError("Only an active season can be finished.")
    if target.status == "finished":
        return
    season_id = target.id
    pending = db.scalar(
        select(func.count()).select_from(Wager).where(Wager.season_id == season_id, Wager.status == "pending")
    )
    unfinished = db.scalar(
        select(func.count())
        .select_from(Match)
        .where(Match.season_id == season_id, Match.settlement_status == "pending")
    )
    if pending or unfinished:
        raise PointOperationError("Season has unfinished matches, settlement or refunds.")
    for member in lock_member_rows(db, db.scalars(
        select(Member.id).where(select(PointTransaction.id).where(
            PointTransaction.member_id == Member.id, PointTransaction.season_id == season_id).exists()).order_by(Member.id)
    )):
        verify_member_ledger(db, member, season_id)
    target.status = "finished"
    # The exclusive season lock excludes contract creation and new stakes.
    for contract in db.scalars(select(CheerContract).where(
        CheerContract.season_id == season_id, CheerContract.finalized_at.is_(None)
    ).order_by(CheerContract.id).with_for_update().execution_options(populate_existing=True)):
        contract.finalized_at = datetime.now(timezone.utc)
    record_event(
        db,
        "season.finished",
        season_id=season_id,
        actor_member_id=actor_member_id,
        request_id=request_id,
        event_key=f"season:{season_id}:finished",
    )
    db.flush()



def apply_match_points(db: Session, *, member_id: int, match_id: int, amount: int, reason: str,
                       idempotency_key: str, related_entry_id: int | None = None,
                       selected_side: int | None = None, request_id: UUID | None = None):
    match = db.get(Match, match_id)
    if match is None:
        raise PointOperationError("Match does not exist.")
    return apply_points(db, member_id=member_id, season_id=match.season_id, amount=amount,
        reason=reason,idempotency_key=idempotency_key,match_id=match_id,
        related_entry_id=related_entry_id,selected_side=selected_side,request_id=request_id)

