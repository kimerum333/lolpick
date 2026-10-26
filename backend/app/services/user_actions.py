"""User actions use server identities and the existing immutable point ledger.

Caller owns the transaction. Lock order: season, match (if any), session,
member, contract. The member lock serializes contract selection and first bets.
"""
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from app.core.auth import AuthError
from app.db.compact import key_hash, point_key_hash
from app.models import CheerContract, Match, PointTransaction, Season, SystemLog, Team, Wager
from app.schemas.user_actions import BetOut, BetPage, CheerOut, MatchContextOut
from app.services import sessions
from app.services.activity import record_event
from app.services.member_locks import lock_member_rows
from app.services.members import matching_member
from app.services.points import apply_points, season_balance, verify_member_ledger


def reject(code, message, status=409):
    raise HTTPException(status, detail={"code": code, "message": message})


def locked_user(db, user):
    member = matching_member(db, user)
    if member is None:
        raise AuthError(404, "member_not_registered", "Register a fresh Google login.")
    row = sessions.session_for_user(db, user)
    if row is None:
        raise AuthError(401, "session_missing", "Perform a new Google login.")
    error = sessions.session_error(row, sessions.now_utc())
    if error:
        raise error
    sessions.native_session_created(db, user)
    locked = lock_member_rows(db, [member.id])[0]
    verify_member_ledger(db, locked)
    return locked


def season_by_code(db, code, *, lock=True):
    query = select(Season).where(Season.public_code == code)
    if lock:
        query = query.with_for_update(read=True)
    season = db.scalar(query.execution_options(populate_existing=True))
    if season is None:
        reject("season_not_found", "Season not found.", 404)
    return season


def cheer_output(db, contract):
    counts = dict(db.execute(select(PointTransaction.cheer_action, func.count()).where(
        PointTransaction.contract_id == contract.id, PointTransaction.reason == "bet_stake"
    ).group_by(PointTransaction.cheer_action)).all())
    return CheerOut(id=contract.id, seasonId=db.get(Season, contract.season_id).public_code,
        teamId=db.get(Team, contract.team_id).external_team_id,
        sealHeld=contract.seal_revoked_at is None,
        sealRevokedAt=sessions.utc(contract.seal_revoked_at) if contract.seal_revoked_at else None,
        finalizedAt=sessions.utc(contract.finalized_at) if contract.finalized_at else None,
        cheeringCount=counts.get("cheering", 0), betrayalCount=counts.get("betrayal", 0),
        createdAt=sessions.utc(contract.created_at))


def get_cheer(db, user, season_code):
    season = season_by_code(db, season_code)
    member = locked_user(db, user)
    contract = db.scalar(select(CheerContract).where(
        CheerContract.member_id == member.id, CheerContract.season_id == season.id))
    return cheer_output(db, contract) if contract else None


def choose_cheer(db, user, body, operation_id, request_id=None):
    season = season_by_code(db, body.seasonId)
    member = locked_user(db, user)
    event_key = f"cheer:{member.id}:{operation_id}"
    logged = db.scalar(select(SystemLog).where(SystemLog.event_key_hash == key_hash(event_key)))
    if logged:
        contract = db.get(CheerContract, logged.context["entry_id"])
        if contract.season_id != season.id or db.get(Team, contract.team_id).external_team_id != body.teamId:
            reject("idempotency_conflict", "Use a new key for a different request.")
        return cheer_output(db, contract)
    contract = db.scalar(select(CheerContract).where(
        CheerContract.member_id == member.id, CheerContract.season_id == season.id).with_for_update().execution_options(populate_existing=True))
    if contract:
        if db.get(Team, contract.team_id).external_team_id != body.teamId:
            reject("cheer_team_locked", "The season's cheer team cannot be changed.")
        # Choosing the same team is harmless, including a retry after closure.
    else:
        if season.status not in {"upcoming", "active"} or season.is_demo:
            reject("cheer_closed", "Choose a team in an upcoming or active season.")
        if db.scalar(select(PointTransaction.id).where(PointTransaction.member_id == member.id,
            PointTransaction.season_id == season.id, PointTransaction.reason == "bet_stake").limit(1)):
            reject("first_bet_already_placed", "Choose a cheer team before the season's first bet.")
        candidates = list(db.scalars(select(Team).where(Team.external_team_id == body.teamId,
            select(Match.id).where(Match.season_id == season.id, Match.is_demo.is_(False),
                or_(Match.team_a_id == Team.id, Match.team_b_id == Team.id)).exists())))
        if len(candidates) != 1:
            reject("invalid_cheer_team", "Choose a confirmed team participating in this season.", 422)
        # Only granted columns are written; timestamps come from the server.
        contract = CheerContract(member_id=member.id, season_id=season.id, team_id=candidates[0].id)
        db.add(contract)
        db.flush()
    record_event(db, "cheer.contracted", season_id=season.id, actor_member_id=member.id,
        request_id=request_id, event_key=event_key, context={"entry_id": contract.id})
    return cheer_output(db, contract)


def bet_output(db, stake_id, *, with_context=False):
    wager = db.get(Wager, stake_id, populate_existing=True)
    result = BetOut(id=wager.id, seasonId=db.get(Season, wager.season_id).public_code,
        matchId=db.get(Match, wager.match_id).public_code,
        teamId=db.get(Team, wager.selected_team_id).external_team_id,
        amount=wager.amount, status=wager.status, payout=wager.payout,
        cheerAction=wager.cheer_action, createdAt=sessions.utc(wager.created_at))
    if with_context:
        from app.models import Member
        result.currentPoints = season_balance(db, db.get(Member, wager.member_id), db.get(Season, wager.season_id))
        contract = db.scalar(select(CheerContract).where(CheerContract.member_id == wager.member_id,
            CheerContract.season_id == wager.season_id))
        result.cheer = cheer_output(db, contract) if contract else None
    return result


def place_bet(db, user, body, operation_id, request_id=None):
    # Resolve without locking, then acquire the shared season lock before match.
    identity = db.execute(select(Match.id, Match.season_id).where(Match.public_code == body.matchId)).first()
    if identity is None:
        reject("match_not_found", "Match not found.", 404)
    db.scalar(select(Season).where(Season.id == identity.season_id).with_for_update(read=True).execution_options(populate_existing=True))
    match = db.scalar(select(Match).where(Match.id == identity.id).with_for_update().execution_options(populate_existing=True))
    member = locked_user(db, user)
    side = (1 if match.team_a and match.team_a.external_team_id == body.teamId else
            2 if match.team_b and match.team_b.external_team_id == body.teamId else None)
    if side is None:
        reject("invalid_bet_team", "Choose a confirmed team in this match.", 422)
    digest = point_key_hash(member.id, match.season_id, "bet_stake", str(operation_id))
    existing = db.scalar(select(PointTransaction).where(PointTransaction.idempotency_key_hash == digest))
    if existing:
        if (existing.match_id, existing.selected_side, -existing.amount) != (match.id, side, body.amount):
            reject("idempotency_conflict", "Use a new key for a different request.")
        return bet_output(db, existing.id, with_context=True)
    contract = db.scalar(select(CheerContract).where(CheerContract.member_id == member.id,
        CheerContract.season_id == match.season_id).with_for_update().execution_options(populate_existing=True))
    if contract is None:
        reject("cheer_required", "Choose a season cheer team before your first bet.")
    action = None
    if contract.team_id in (match.team_a_id, match.team_b_id):
        action = "cheering" if contract.team_id == (match.team_a_id if side == 1 else match.team_b_id) else "betrayal"
    entry = apply_points(db, member_id=member.id, season_id=match.season_id, match_id=match.id,
        selected_side=side, amount=-body.amount, reason="bet_stake", idempotency_key=str(operation_id),
        contract_id=contract.id if action else None, cheer_action=action, request_id=request_id)
    if action == "betrayal" and contract.seal_revoked_at is None:
        contract.seal_revoked_at = sessions.now_utc()
    record_event(db, "bet.placed", season_id=match.season_id, actor_member_id=member.id,
        request_id=request_id, event_key=f"bet:{entry.id}", context={"entry_id": entry.id, "match_id": match.id})
    db.flush()
    return bet_output(db, entry.id, with_context=True)


def list_bets(db, user, season_code, limit, offset):
    member = locked_user(db, user)
    filters = [Wager.member_id == member.id]
    if season_code is not None:
        season_id = db.scalar(select(Season.id).where(Season.public_code == season_code))
        if season_id is None:
            reject("season_not_found", "Season not found.", 404)
        filters.append(Wager.season_id == season_id)
    total = db.scalar(select(func.count()).select_from(Wager).where(*filters))
    # Project only response columns in one joined query, avoiding ORM relationship
    # loading and a separate Wager/Match/Season/Team round trip for every row.
    rows = db.execute(select(Wager.id, Season.public_code, Match.public_code,
        Team.external_team_id, Wager.amount, Wager.status, Wager.payout,
        Wager.cheer_action, Wager.created_at).join(Season, Wager.season_id == Season.id)
        .join(Match, Wager.match_id == Match.id).join(Team, Wager.selected_team_id == Team.id)
        .where(*filters).order_by(Wager.id.desc()).limit(limit).offset(offset)).all()
    items = [BetOut(id=identity, seasonId=season, matchId=match, teamId=team,
        amount=amount, status=status, payout=payout, cheerAction=action,
        createdAt=sessions.utc(created))
        for identity, season, match, team, amount, status, payout, action, created in rows]
    return BetPage(items=items, total=total, limit=limit, offset=offset)


def match_context(db, user, match_code):
    from app.services.catalog import get_match
    match = db.scalar(select(Match).where(Match.public_code == match_code))
    if match is None:
        reject("match_not_found", "Match not found.", 404)
    season = season_by_code(db, match.season_row.public_code)
    member = locked_user(db, user)
    contract = db.scalar(select(CheerContract).where(CheerContract.member_id == member.id,
        CheerContract.season_id == season.id))
    stake_id = db.scalar(select(PointTransaction.id).where(PointTransaction.member_id == member.id,
        PointTransaction.match_id == match.id, PointTransaction.reason == "bet_stake"))
    public = get_match(db, match_code)
    balance = season_balance(db, member, season)
    reason = public.bettingClosedReason if not public.bettingOpen else (
        "bet_already_placed" if stake_id is not None else "cheer_required" if contract is None else
        "insufficient_points" if balance <= 0 else None)
    row = sessions.session_for_user(db, user)
    my_rank = None
    if stake_id is not None:
        from sqlalchemy.orm import aliased
        stake = db.get(PointTransaction, stake_id)
        refund = aliased(PointTransaction)
        refunded = select(refund.id).where(refund.related_entry_id == PointTransaction.id,
            refund.reason == "bet_refund").correlate(PointTransaction).exists()
        ranked = select(PointTransaction.member_id, func.row_number().over(order_by=(
            PointTransaction.amount, PointTransaction.created_at, PointTransaction.id)).label("position")).where(
            PointTransaction.match_id == match.id, PointTransaction.reason == "bet_stake",
            PointTransaction.selected_side == stake.selected_side, ~refunded).subquery()
        my_rank = db.scalar(select(ranked.c.position).where(ranked.c.member_id == member.id))
    return MatchContextOut(matchId=match_code, seasonId=season.public_code, currentPoints=balance,
        cheer=cheer_output(db, contract) if contract else None,
        bet=bet_output(db, stake_id) if stake_id is not None else None,
        bettingOpen=public.bettingOpen, eligible=reason is None, reason=reason,
        sessionExpiresAt=sessions.utc(row.expires_at), myRank=my_rank)
