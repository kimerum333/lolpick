from datetime import datetime, time, timedelta, timezone
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased
from app.models import ApiSource, CheerContract, Match, PointTransaction, Season, Team
from app.core.config import get_settings
from app.schemas.catalog import EventOut, EventStageOut, MatchOut, MatchPage, SeasonOut, StakeRankingOut, StakeRankOut, TeamOut


def utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def team_output(match: Match, team_id: int | None) -> TeamOut | None:
    team = match.team_a if team_id == match.team_a_id else match.team_b
    if team is None:
        return None
    row = team.display_data or {}
    return TeamOut(
        id=team.external_team_id, name=team.name,
        region=row.get("region", ""), color=row.get("color", "#64748b"),
        logo=team.logo_url, acronym=team.acronym, location=row.get("location"),
        apiSource=match.api_source,
    )


def list_teams(db: Session, season_id: str | None = None) -> list[TeamOut]:
    # Team names and logos reflect the current provider display.
    if season_id is not None:
        season_id = db.scalar(select(Season.id).where(Season.public_code == season_id))
        if season_id is None:
            return []
        season_ids = [season_id]
    else:
        season_ids = list(db.scalars(select(Season.id).where(Season.status == "active")))
    if not season_ids:
        return []
    participating = select(Match.id).where(Match.season_id.in_(season_ids),
        or_(Match.team_a_id == Team.id, Match.team_b_id == Team.id)).exists()
    rows = db.execute(select(Team.external_team_id, Team.name, Team.acronym,
        Team.logo_url, Team.display_data, ApiSource.code).join(ApiSource, Team.api_source_id == ApiSource.id)
        .where(participating).order_by(ApiSource.code, Team.id)).all()
    return [TeamOut(id=identity, name=name, acronym=acronym, logo=logo,
        region=(display or {}).get("region", ""), color=(display or {}).get("color", "#64748b"),
        location=(display or {}).get("location"), apiSource=source)
        for identity, name, acronym, logo, display, source in rows]



def list_seasons(db: Session, year: int | None = None, event_id: str | None = None) -> list[SeasonOut]:
    selected = select(Season)
    if year is not None:
        selected = selected.where(Season.year == year)
    if event_id is not None:
        selected = selected.where(Season.public_code == event_id)
    rows = list(db.scalars(selected.order_by(Season.year.desc(), Season.id)))
    memberships: dict[str, set[str]] = {}
    for season_id, team_a, team_b in db.execute(select(Match.season_id, Match.team_a_id, Match.team_b_id).where(
            Match.season_id.in_([row.id for row in rows]))):
        memberships.setdefault(season_id, set()).update(identity for identity in (team_a, team_b) if identity is not None)
    identities = {identity for values in memberships.values() for identity in values}
    teams = dict(db.execute(select(Team.id, Team.external_team_id).where(Team.id.in_(identities))).all()) if identities else {}
    return [
        SeasonOut(
            id=row.public_code,
            slug=row.slug,
            name=row.name,
            year=row.year,
            label=row.label or row.name,
            active=row.status == "active",
            status=row.status,
            teams=sorted({teams[identity] for identity in memberships.get(row.id, set())}),
            initialPoints=row.initial_points,
            isDemo=row.is_demo,
            startsAt=int(utc_datetime(row.starts_at).timestamp()*1000) if row.starts_at else None,
            endsAt=int(utc_datetime(row.ends_at).timestamp()*1000) if row.ends_at else None,
        )
        for row in rows
    ]


def serialize_match(
    match: Match, pools: tuple[int, int], now: datetime, *, season_status: str | None = None
) -> MatchOut:
    starts_at = utc_datetime(match.starts_at) if match.starts_at is not None else None
    data = match.api_data if isinstance(match.api_data, dict) else {}
    normalized = data.get("normalized", {})
    if not isinstance(normalized, dict):
        normalized = {}
    raw_issues = normalized.get("issues", [])
    issues = (
        [item for item in raw_issues if isinstance(item, str)]
        if isinstance(raw_issues, list)
        else ["invalid_provider_issues"]
    )
    synced_at = utc_datetime(match.api_synced_at) if match.api_synced_at else None
    provider_status = normalized.get("source_status")
    if match.api_source == "pandascore" and (
        synced_at is None
        or (utc_datetime(now) - synced_at).total_seconds() > get_settings().pandascore_max_staleness_seconds
    ):
        issues.append("provider_data_stale")
    from app.services.betting_rules import betting_closed_reason
    reason = betting_closed_reason(match, now=now, season_status=season_status)
    betting_open = reason is None
    return MatchOut(
        id=match.public_code,
        event=match.season_row.public_code,
        season=match.season_row.public_code,
        round=match.round,
        teams=(match.team_a.external_team_id if match.team_a else None,
               match.team_b.external_team_id if match.team_b else None),
        teamDetails=(team_output(match, match.team_a_id), team_output(match, match.team_b_id)),
        pools=pools,
        startsAt=int(starts_at.timestamp() * 1000) if starts_at else None,
        status="settled" if match.settlement_status == "settled" else match.status,
        score=(match.score_a, match.score_b) if match.score_a is not None else None,
        winner=(
            match.team_a.external_team_id
            if match.result == "team_a_win" and match.team_a is not None
            else match.team_b.external_team_id
            if match.result == "team_b_win" and match.team_b is not None
            else None
        ),
        bettingOpen=betting_open,
        seasonStatus=season_status,
        result=match.result,
        settlementStatus=match.settlement_status or "pending",
        settlementPool=match.settlement_pool,
        settlementPaid=match.settlement_paid,
        removedRemainder=match.settlement_remainder,
        isDemo=match.is_demo,
        poolSource="demo" if match.is_demo else "bets",
        apiSource=match.api_source,
        providerStatus=provider_status,
        apiSyncedAt=int(synced_at.timestamp() * 1000) if synced_at else None,
        name=normalized.get("name", ""),
        matchType=normalized.get("match_type"),
        numberOfGames=normalized.get("number_of_games"),
        league=normalized.get("league"),
        tournament=normalized.get("tournament"),
        forfeit=normalized.get("forfeit", False),
        draw=match.result == "draw" if match.result is not None else normalized.get("draw", False),
        dataIssues=sorted(set(issues)),
        bettingClosedReason=reason,
    )


def list_matches(
    db: Session,
    season_id: str | None,
    status: str | None,
    limit: int,
    offset: int,
    *,
    api_source: str | None = None,
    is_demo: bool | None = None,
    team_id: str | None = None,
    match_date=None,
    unscheduled: bool | None = None,
) -> MatchPage:
    conditions = []
    if season_id:
        conditions.append(Match.season_id == select(Season.id).where(Season.public_code == season_id).scalar_subquery())
    if status:
        conditions.append(Match.settlement_status == "settled" if status == "settled" else Match.status == status)
        if status == "finished":
            conditions.append(Match.settlement_status != "settled")
    if api_source:
        conditions.append(Match.api_source == api_source)
    if is_demo is not None:
        conditions.append(Match.is_demo == is_demo)
    if team_id:
        identities = select(Team.id).where(Team.external_team_id == team_id)
        conditions.append(or_(Match.team_a_id.in_(identities), Match.team_b_id.in_(identities)))
    if match_date is not None:
        beginning = datetime.combine(match_date, time.min, tzinfo=timezone(timedelta(hours=9)))
        conditions.extend((Match.starts_at >= beginning.astimezone(timezone.utc),
                           Match.starts_at < (beginning + timedelta(days=1)).astimezone(timezone.utc)))
    if unscheduled is not None:
        conditions.append(Match.starts_at.is_(None) if unscheduled else Match.starts_at.is_not(None))
    total = db.scalar(select(func.count()).select_from(Match).where(*conditions)) or 0
    matches = list(
        db.scalars(select(Match).where(*conditions).order_by(Match.starts_at, Match.id).limit(limit).offset(offset))
    )
    return MatchPage(items=serialize_matches(db, matches), total=total, limit=limit, offset=offset)


def serialize_matches(db: Session, matches: list[Match]) -> list[MatchOut]:
    pools: dict[tuple[str, str], int] = {}
    ids = [match.id for match in matches if not match.is_demo]
    if ids:
        refund = aliased(PointTransaction)
        refunded = select(refund.id).where(
            refund.related_entry_id == PointTransaction.id, refund.reason == "bet_refund"
        ).correlate(PointTransaction).exists()
        for match_id, side, amount in db.execute(
            select(PointTransaction.match_id, PointTransaction.selected_side, func.sum(-PointTransaction.amount))
            .where(PointTransaction.match_id.in_(ids), PointTransaction.reason == "bet_stake", ~refunded)
            .group_by(PointTransaction.match_id, PointTransaction.selected_side)
        ):
            pools[(match_id, side)] = int(amount)
    now = datetime.now(timezone.utc)
    seasons = dict(
        db.execute(select(Season.id, Season.status).where(Season.id.in_({match.season_id for match in matches}))).all()
    )
    return [
        serialize_match(
            match,
            (match.demo_pool_a, match.demo_pool_b)
            if match.is_demo
            else (pools.get((match.id, 1), 0), pools.get((match.id, 2), 0)),
            now,
            season_status=seasons.get(match.season_id),
        )
        for match in matches
    ]


def get_match(db: Session, match_id: str) -> MatchOut | None:
    match = db.scalar(select(Match).where(Match.public_code == match_id))
    return serialize_matches(db, [match])[0] if match is not None else None


def get_event(db: Session, event_id: str) -> EventOut | None:
    seasons = list_seasons(db, event_id=event_id)
    if not seasons:
        return None
    season_id = select(Season.id).where(Season.public_code == event_id).scalar_subquery()
    rows = db.execute(select(Match.round, func.min(Match.starts_at), func.max(Match.starts_at),
        func.count(), func.sum(case_unscheduled())).where(Match.season_id == season_id)
        .group_by(Match.round).order_by(func.min(Match.starts_at).asc().nulls_last(), Match.round))
    stages = [EventStageOut(name=name,
        startsAt=int(utc_datetime(start).timestamp()*1000) if start else None,
        endsAt=int(utc_datetime(end).timestamp()*1000) if end else None,
        matchCount=count, unscheduledCount=int(unknown)) for name, start, end, count, unknown in rows]
    return EventOut(**seasons[0].model_dump(), stages=stages)


def case_unscheduled():
    from sqlalchemy import case
    return case((Match.starts_at.is_(None), 1), else_=0)


def stake_ranking(db: Session, match_id: str) -> StakeRankingOut | None:
    match = db.scalar(select(Match).where(Match.public_code == match_id))
    if match is None:
        return None
    output = serialize_matches(db, [match])[0]
    results = []
    refund = aliased(PointTransaction)
    refunded = select(refund.id).where(refund.related_entry_id == PointTransaction.id,
        refund.reason == "bet_refund").correlate(PointTransaction).exists()
    for side in (1, 2):
        statement = select(-PointTransaction.amount, CheerContract.seal_revoked_at, CheerContract.id).outerjoin(
            CheerContract, (PointTransaction.member_id == CheerContract.member_id)
            & (PointTransaction.season_id == CheerContract.season_id)).where(
            PointTransaction.match_id == match.id, PointTransaction.reason == "bet_stake",
            PointTransaction.selected_side == side, ~refunded).order_by(
            PointTransaction.amount, PointTransaction.created_at, PointTransaction.id).limit(5)
        rows = [] if match.is_demo else db.execute(statement).all()
        pool = output.pools[side-1]
        results.append([StakeRankOut(rank=rank, amount=int(amount),
            shareBasisPoints=int(amount)*10000//pool if pool else 0,
            sealHeld=identity is not None and revoked is None)
            for rank, (amount, revoked, identity) in enumerate(rows, 1)])
    return StakeRankingOut(matchId=output.id, seasonId=output.season,
        teams=output.teams, pools=output.pools, items=tuple(results))
