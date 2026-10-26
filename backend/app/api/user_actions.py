from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from app.api.auth import Db, SessionUser
from app.api.validation import validate_api_request
from app.schemas.user_actions import BetOut, BetPage, BetRequest, CheerOut, CheerRequest, MatchContextOut
from app.services import user_actions
from app.models import Match, Season, Team
from sqlalchemy import select

router = APIRouter(prefix="/api/v1/auth", tags=["user actions"], dependencies=[Depends(validate_api_request)])
Operation = Annotated[UUID, Header(alias="Idempotency-Key")]


def mark_input(request, db, body):
    # Only DB-confirmed public IDs can enter diagnostic files; an attacker cannot
    # smuggle an email/token into a valid-looking identifier field.
    safe = {}
    for name, model, column in (('matchId',Match,Match.public_code),
                                ('seasonId',Season,Season.public_code),
                                ('teamId',Team,Team.external_team_id)):
        value = getattr(body,name,None)
        if value is not None and db.scalar(select(model.id).where(column == value).limit(1)) is not None:
            safe[name] = value
    request.state.audit_safe_body = safe


def private_response(response):
    response.headers["Cache-Control"] = "no-store"


@router.post("/cheer", response_model=CheerOut, summary="Choose an immutable season cheer team before the first bet")
def choose_cheer(request: Request, response: Response, body: CheerRequest, operation_id: Operation,
                 user: SessionUser, db: Db):
    with db.begin():
        mark_input(request, db, body)
        result = user_actions.choose_cheer(db, user, body, operation_id, getattr(request.state, "request_id", None))
        request.state.log_season_id = user_actions.season_by_code(db, body.seasonId, lock=False).id
    private_response(response)
    return result


@router.get("/matches/{match_id}/context", response_model=MatchContextOut,
            summary="Read your private, explicitly scoped match context")
def match_context(match_id: str, request: Request, response: Response, user: SessionUser, db: Db):
    with db.begin():
        result = user_actions.match_context(db, user, match_id)
        request.state.log_season_id = user_actions.season_by_code(db, result.seasonId, lock=False).id
        request.state.audit_safe_parameters = {"match_id": match_id}
    private_response(response)
    return result


@router.get("/cheer", response_model=CheerOut | None, summary="Read your own season cheer and seal history")
def get_cheer(request: Request, response: Response, user: SessionUser, db: Db,
              season_id: str = Query(min_length=1, max_length=80)):
    with db.begin():
        result = user_actions.get_cheer(db, user, season_id)
        request.state.log_season_id = user_actions.season_by_code(db, season_id, lock=False).id
        request.state.audit_safe_parameters = {'season_id':season_id}
    private_response(response)
    return result


@router.post("/bets", response_model=BetOut, summary="Place one funded bet per match")
def place_bet(request: Request, response: Response, body: BetRequest, operation_id: Operation,
              user: SessionUser, db: Db):
    with db.begin():
        mark_input(request, db, body)
        result = user_actions.place_bet(db, user, body, operation_id, getattr(request.state, "request_id", None))
        request.state.log_season_id = user_actions.season_by_code(db, result.seasonId, lock=False).id
    private_response(response)
    return result


@router.get("/bets", response_model=BetPage, summary="Read only your own funded betting history")
def list_bets(request: Request, response: Response, user: SessionUser, db: Db,
              season_id: str | None = Query(default=None, min_length=1, max_length=80),
              limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=2147483647)):
    with db.begin():
        result = user_actions.list_bets(db, user, season_id, limit, offset)
        if season_id is not None:
            request.state.log_season_id = user_actions.season_by_code(db, season_id, lock=False).id
            request.state.audit_safe_parameters = {'season_id':season_id,'limit':limit,'offset':offset}
    private_response(response)
    return result
