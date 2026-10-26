"""Explicit administrator operations; roles never come from browser metadata."""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from app.api.auth import Db, SessionUser
from app.models import ADMIN_TYPE, Season, Member
from sqlalchemy import select
from app.services.points import PointOperationError, finish_season, activate_season
from app.services.pandascore_sync import SyncError, synchronize
from app.integrations.pandascore import PandaScoreClient, PandaScoreError
from app.sync_pandascore import collect
from app.api.validation import validate_api_request
from app.schemas.results import MatchResultRequest
from app.core.error_details import error_details
from app.services.settlement import process_result
from app.core.config import get_settings
from app.schemas.system import SeasonActivatedOut, SeasonFinishedOut, SettlementOut, SyncSummaryOut

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(validate_api_request)])


def require_admin(user: SessionUser, db: Db):
    with db.begin():
        member = db.scalar(select(Member).where(Member.auth_user_id == user.user_id))
        member_id = member.id if member is not None else None
        allowed = member is not None and member.type == ADMIN_TYPE
    if not allowed:
        raise HTTPException(403, detail={"code": "admin_required", "message": "Administrator permission required."})
    if get_settings().admin_require_mfa and user.aal != 'aal2':
        raise HTTPException(403, detail={'code':'admin_mfa_required', 'message':'Complete authenticator MFA before administrator operations.'})
    return member_id


Admin = Annotated[int, Depends(require_admin)]


@router.post("/seasons/{season_id}/refresh", response_model=SyncSummaryOut, summary="Manual PandaScore refresh, independent of the hourly worker")
def refresh(season_id: str, admin: Admin, db: Db, request: Request):
    with db.begin():
        season = db.scalar(select(Season).where(Season.public_code == season_id))
        series_id = (
            int(season.external_series_id)
            if season and season.external_series_id and season.external_series_id.isdecimal()
            else None
        )
        if type(series_id) is not int or series_id <= 0:
            raise HTTPException(
                409,
                detail={
                    "code": "series_not_bound",
                    "message": "Bind this season to a series using the operator import first.",
                },
            )
    try:
        # No DB transaction or season locks are held during network collection.
        with PandaScoreClient() as client:
            series, fixtures, collected_at = collect(client, series_id)
        with db.begin():
            return synchronize(
                db,
                season_id=season_id,
                series=series,
                fixtures=fixtures,
                synced_at=collected_at,
                actor_member_id=admin,
                request_id=request.state.request_id,
            )
    except PandaScoreError as exc:
        request.state.error_detail = error_details(exc)
        raise HTTPException(503, detail={"code": exc.code, "message": "PandaScore refresh failed."}) from None
    except SyncError as exc:
        request.state.error_detail = error_details(exc)
        raise HTTPException(409, detail={"code": exc.code, "message": exc.message}) from None


@router.post("/seasons/{season_id}/finish", response_model=SeasonFinishedOut, summary="Finish a season explicitly after all settlement/refunds")
def finish(season_id: str, admin: Admin, db: Db, request: Request):
    try:
        with db.begin():
            finish_season(db, season_id, actor_member_id=admin, request_id=request.state.request_id)
        return {"seasonId": season_id, "status": "finished"}
    except PointOperationError as exc:
        request.state.error_detail = error_details(exc)
        raise HTTPException(409, detail={"code": "season_not_ready", "message": str(exc)}) from None


@router.post("/seasons/{season_id}/activate", response_model=SeasonActivatedOut, summary="Activate an upcoming season and open its balances once")
def activate(season_id: str, admin: Admin, db: Db, request: Request):
    try:
        with db.begin():
            count = activate_season(db, season_id, actor_member_id=admin, request_id=request.state.request_id)
        return {"seasonId": season_id, "status": "active", "initializedMembers": count}
    except PointOperationError as exc:
        request.state.error_detail = error_details(exc)
        raise HTTPException(409, detail={"code": "season_not_ready", "message": str(exc)}) from None


@router.post("/matches/{match_id}/result", response_model=SettlementOut, summary="Record a result and atomically settle an active-season match")
def match_result(match_id: str, payload: MatchResultRequest, admin: Admin, db: Db, request: Request):
    try:
        with db.begin():
            return process_result(
                db,
                match_id,
                payload.result,
                score_a=payload.score_a,
                score_b=payload.score_b,
                actor_member_id=admin,
                request_id=request.state.request_id,
            )
    except PointOperationError as exc:
        request.state.error_detail = error_details(exc)
        raise HTTPException(409, detail={"code": "result_not_ready", "message": str(exc)}) from None
