from datetime import date
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.api.auth import optional_server_session
from app.db.readiness import DatabaseReadinessError, verify_runtime
from app.api.validation import validate_api_request
from app.models import Season
from app.schemas.catalog import EventOut, MatchOut, MatchPage, SeasonOut, StakeRankingOut, TeamOut
from app.services import catalog
from app.schemas.system import HealthOut, ReadyOut

router = APIRouter(prefix="/api/v1", dependencies=[Depends(validate_api_request)])
Db = Annotated[Session, Depends(get_db)]


@router.get("/health", tags=["health"], response_model=HealthOut)
def health():
    return {"status": "ok", "service": "lolpick-backend", "version": "0.14.1"}


@router.get("/ready", tags=["health"], response_model=ReadyOut)
def ready(db: Db):
    from app.core.config import get_settings
    try:
        revision = verify_runtime(db, require_restricted_role=get_settings().app_env == "production")
    except DatabaseReadinessError as exc:
        raise HTTPException(
            503, detail={"code": exc.code, "message": "Check v7 database migrations and backend role permissions."}
        ) from None
    return {"status": "ok", "database": "connected", "migration": revision}


@router.get("/teams", response_model=list[TeamOut], tags=["catalog"], dependencies=[Depends(optional_server_session)])
def teams(db: Db, request: Request, season_id: str | None = Query(default=None, max_length=80)):
    request.state.log_season_id = (
        db.scalar(select(Season.id).where(Season.public_code == season_id)) if season_id is not None else None
    )
    return catalog.list_teams(db, season_id)


@router.get(
    "/seasons", response_model=list[SeasonOut], tags=["catalog"], dependencies=[Depends(optional_server_session)]
)
@router.get(
    "/events", response_model=list[SeasonOut], tags=["catalog"], dependencies=[Depends(optional_server_session)]
)
def seasons(db: Db, year: int | None = Query(default=None, ge=2000, le=9999)):
    return catalog.list_seasons(db, year=year)


@router.get("/events/{event_id}", response_model=EventOut, tags=["catalog"],
            dependencies=[Depends(optional_server_session)])
def event_detail(event_id: str, db: Db, request: Request):
    result = catalog.get_event(db, event_id)
    if result is None:
        raise HTTPException(404, detail={"code": "season_not_found", "message": "Event not found."})
    request.state.log_season_id = db.scalar(select(Season.id).where(Season.public_code == event_id))
    return result


@router.get("/matches", response_model=MatchPage, tags=["catalog"], dependencies=[Depends(optional_server_session)])
def matches(
    db: Db,
    request: Request,
    season_id: str | None = Query(default=None, max_length=80),
    event_id: str | None = Query(default=None, max_length=80),
    status: Literal["scheduled", "live", "finished", "settled", "cancelled"] | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=2_147_483_647),
    api_source: str | None = Query(default=None, min_length=1, max_length=40),
    is_demo: bool | None = None,
    team_id: str | None = Query(default=None, min_length=1, max_length=80),
    match_date: date | None = Query(default=None, alias="date"),
    unscheduled: bool | None = None,
):
    if season_id is not None and event_id is not None and season_id != event_id:
        raise HTTPException(422, detail="season_id and event_id must refer to the same season.")
    selected = season_id or event_id
    if match_date is not None and (unscheduled is True or not 2000 <= match_date.year <= 9998):
        raise HTTPException(422, detail={"code": "invalid_request", "message": "Use a valid date or unscheduled=true separately."})
    request.state.log_season_id = (
        db.scalar(select(Season.id).where(Season.public_code == selected)) if selected is not None else None
    )
    return catalog.list_matches(
        db, selected, status, limit, offset, api_source=api_source, is_demo=is_demo, team_id=team_id,
        match_date=match_date, unscheduled=unscheduled,
    )


@router.get(
    "/matches/{match_id}", response_model=MatchOut, tags=["catalog"], dependencies=[Depends(optional_server_session)]
)
def match_detail(match_id: str, db: Db, request: Request):
    result = catalog.get_match(db, match_id)
    if result is None:
        raise HTTPException(404, detail={"code": "match_not_found", "message": "Match not found."})
    request.state.log_season_id = db.scalar(select(Season.id).where(Season.public_code == result.season))
    return result


@router.get("/matches/{match_id}/stake-ranking", response_model=StakeRankingOut, tags=["catalog"],
            dependencies=[Depends(optional_server_session)])
def stake_ranking(match_id: str, db: Db, request: Request):
    result = catalog.stake_ranking(db, match_id)
    if result is None:
        raise HTTPException(404, detail={"code": "match_not_found", "message": "Match not found."})
    request.state.log_season_id = db.scalar(select(Season.id).where(Season.public_code == result.seasonId))
    return result
