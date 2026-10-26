from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import select
from app.models import Season
from sqlalchemy.orm import Session
from app.core.auth import AuthenticatedUser, AuthError, get_authenticated_user, bearer
from fastapi.security import HTTPAuthorizationCredentials
from app.db.session import get_db
from app.schemas.auth import MemberOut
from app.services.members import matching_member, synchronize_member
from app.services import sessions
from app.api.validation import validate_api_request
from app.api.resource_limits import enforce_identity_limits

router = APIRouter(prefix="/api/v1/auth", tags=["auth"], dependencies=[Depends(validate_api_request)])
User = Annotated[AuthenticatedUser, Depends(get_authenticated_user)]
Db = Annotated[Session, Depends(get_db)]


def member_output(db, member, user, season_code=None):
    from app.services.points import season_balance
    active = list(db.scalars(select(Season).where(Season.status == "active", Season.is_demo.is_(False)).order_by(Season.id)))
    if season_code is not None:
        selected = db.scalar(select(Season).where(Season.public_code == season_code))
        if selected is None:
            raise HTTPException(404, detail={"code": "season_not_found", "message": "Season not found."})
    elif len(active) == 1:
        selected = active[0]
    elif not active:
        selected = member.current_season
    else:
        selected = None  # Multiple seasons require an explicit UI context.
    row = sessions.session_for_user(db, user)
    return MemberOut(
        memberId=member.auth_user_id,
        currentPoints=season_balance(db, member, selected) if selected else 0,
        currentSeasonId=selected.public_code if selected else None,
        availableSeasonIds=[season.public_code for season in active],
        sessionExpiresAt=sessions.utc(row.expires_at) if row else None,
    )


def mark_actor(request, member):
    request.state.actor_member_id = member.id


def require_server_session(request: Request, user: User, db: Db) -> AuthenticatedUser:
    error = None
    with db.begin():
        member = matching_member(db, user)
        if member is None:
            error = AuthError(404, "member_not_registered", "Perform a new Google login and register the session.")
        else:
            mark_actor(request, member)
            row = sessions.session_for_user(db, user)
            if row is None:
                error = AuthError(401, "session_missing", "Perform a new Google login.")
            else:
                error = sessions.session_error(row, sessions.now_utc())
                if error is None:
                    try:
                        sessions.native_session_created(db, user)
                    except AuthError as exc:
                        sessions.revoke_session(row)
                        error = exc
    # Expired/revoked states must commit even when this request is rejected.
    if error:
        raise error
    enforce_identity_limits(request, user)
    return user


def optional_server_session(
    request: Request, db: Db, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
):
    if credentials is None:
        if request.headers.get("authorization") is not None:
            raise AuthError(401, "invalid_token", "Use a valid Bearer credential.")
        return None  # Anonymous catalog requests stay public.
    user = get_authenticated_user(credentials)
    return require_server_session(request, user, db)


SessionUser = Annotated[AuthenticatedUser, Depends(require_server_session)]


@router.post(
    "/session", response_model=MemberOut, summary="Register a fresh Google login; never extend an existing session"
)
def session(
    request: Request,
    response: Response,
    user: User,
    db: Db,
    operation_id: Annotated[UUID, Header(alias="Idempotency-Key")],
):
    error, result = None, None
    enforce_identity_limits(request, user)
    with db.begin():
        native_created = sessions.native_session_created(db, user)
        # Activation gate -> active seasons -> registration identity -> session -> member.
        # The identity gate also serializes two registrations of an absent row.
        from app.services.season_locks import registration_identity, registration_seasons
        registration_seasons(db)
        registration_identity(db, user)
        row = sessions.session_for_user(db, user)
        if row is not None:
            error = sessions.session_error(row, sessions.now_utc())
        elif (
            user.oauth_at is None
            or not sessions.timedelta(0)
            <= sessions.now_utc() - sessions.utc(native_created)
            <= sessions.REGISTRATION_WINDOW
            or not sessions.timedelta(0)
            <= sessions.now_utc() - sessions.utc(user.oauth_at)
            <= sessions.REGISTRATION_WINDOW
        ):
            error = AuthError(401, "fresh_google_login_required", "Perform a new Google login before registration.")
        if error is None:
            member = synchronize_member(
                db, user, operation_id=operation_id, request_id=getattr(request.state, "request_id", None)
            )
            sessions.register_session(db, user, row=row, native_created=native_created)
            mark_actor(request, member)
            result = member_output(db, member, user)
    if error:
        raise error
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/me", response_model=MemberOut, summary="Read points with a live server session")
def me(request: Request, response: Response, user: SessionUser, db: Db,
       season_id: str | None = Query(default=None, min_length=1, max_length=80)):
    from app.services.user_actions import locked_user
    with db.begin():
        member = locked_user(db, user)
        result = member_output(db, member, user, season_id)
        if result.currentSeasonId is not None:
            request.state.log_season_id = db.scalar(select(Season.id).where(Season.public_code == result.currentSeasonId))
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/logout", status_code=204, summary="Revoke this server session permanently")
def logout(request: Request, user: SessionUser, db: Db):
    with db.begin():
        row = sessions.session_for_user(db, user)
        sessions.revoke_session(row)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
