"""Internal, read-only reports. Member UUIDs must not be exposed publicly."""

from datetime import timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.models import Wager, Match, PointTransaction, Season


class PointReportError(ValueError):
    pass


def match_stake_ranking(
    db: Session,
    *,
    season_id: str,
    match_id: str,
    order_by: str = "amount",
    include_refunded: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    if (
        not isinstance(season_id, str)
        or not 1 <= len(season_id) <= 80
        or not isinstance(match_id, str)
        or not 1 <= len(match_id) <= 80
    ):
        raise PointReportError("Provide a season and match ID.")
    if (
        order_by not in {"amount", "time"}
        or type(include_refunded) is not bool
        or type(limit) is not int
        or not 1 <= limit <= 100
        or type(offset) is not int
        or offset < 0
    ):
        raise PointReportError("Use amount/time ordering and valid pagination.")
    public_season_id, public_match_id = season_id, match_id
    season_id = db.scalar(select(Season.id).where(Season.public_code == season_id))
    match = db.scalar(select(Match).where(Match.public_code == match_id))
    if match is None or match.season_id != season_id:
        raise PointReportError("Match does not belong to the requested season.")
    match_id = match.id
    refund = aliased(PointTransaction)
    has_refund = (
        select(refund.id)
        .where(refund.related_entry_id == PointTransaction.id, refund.reason == "bet_refund")
        .correlate(PointTransaction)
        .exists()
    )
    refunded = has_refund
    conditions = [
        PointTransaction.season_id == season_id,
        PointTransaction.match_id == match_id,
        PointTransaction.reason == "bet_stake",
    ]
    source = PointTransaction.__table__.join(Wager.__table__, Wager.id == PointTransaction.id)
    if not include_refunded:
        conditions.append(~refunded)
    total = db.scalar(select(func.count()).select_from(source).where(*conditions)) or 0
    unfunded = 0  # A wager is a funded ledger entry by construction.
    statement = (
        select(
            PointTransaction.id.label("transaction_id"),
            PointTransaction.member_id,
            PointTransaction.id.label("bet_id"),
            Wager.selected_team_id,
            Wager.status.label("bet_status"),
            (-PointTransaction.amount).label("stake_points"),
            PointTransaction.created_at,
            func.rank().over(order_by=PointTransaction.amount).label("amount_rank"),
            func.row_number().over(order_by=(PointTransaction.created_at, PointTransaction.id)).label("entry_order"),
            refunded.label("refunded"),
        )
        .select_from(source)
        .where(*conditions)
    )
    order = (
        (PointTransaction.amount, PointTransaction.created_at, PointTransaction.id)
        if order_by == "amount"
        else (PointTransaction.created_at, PointTransaction.id)
    )
    rows = db.execute(statement.order_by(*order).limit(limit).offset(offset)).mappings()
    items = []
    for row in rows:
        created = row["created_at"]
        created = created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created.astimezone(timezone.utc)
        items.append(
            {
                "memberId": str(row["member_id"]),
                "betId": str(row["bet_id"]),
                "transactionId": str(row["transaction_id"]),
                "teamId": match.team_a.external_team_id
                if row["selected_team_id"] == match.team_a_id
                else match.team_b.external_team_id,
                "stakePoints": int(row["stake_points"]),
                "betStatus": row["bet_status"],
                "amountRank": int(row["amount_rank"]),
                "entryOrder": int(row["entry_order"]),
                "createdAt": created.isoformat(),
                "refunded": bool(row["refunded"]),
            }
        )
    return {
        "seasonId": public_season_id,
        "matchId": public_match_id,
        "orderBy": order_by,
        "includeRefunded": include_refunded,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "unfundedBets": unfunded,
    }
