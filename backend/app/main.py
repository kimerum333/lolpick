import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import router
from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
from app.api.user_actions import router as user_actions_router
from app.api.validation import RequestEnvelopeMiddleware
from app.api.resource_limits import ResourceLimitsMiddleware
from app.api.public_availability import (PublicAvailabilityMiddleware, PublicSnapshots,
                                         ResponseCORSMiddleware, TEMPORARY_MESSAGE)
from app.core.auth import AuthError, dispose_auth_client
from app.core.config import DatabaseConfigurationError, get_settings
from app.core.error_details import error_details
from app.db.session import dispose_engine, dispose_audit_engine
from app.telemetry import attach_activity_middleware, ReplayWorker
from app.audit_journal import get_journal, JournalUnavailable
from app.services.points import PointOperationError
from app.schemas.system import ErrorOut

logger = logging.getLogger("lolpick")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        get_journal()
    except JournalUnavailable:
        # Request audit admission still blocks all writes. Public snapshots and
        # process health remain available while operators restore the journal.
        logger.error("Audit startup unavailable; protected requests remain blocked.")
    replay = ReplayWorker()
    replay.thread.start()
    try:
        yield
    finally:
        replay.stop.set()
        await asyncio.to_thread(replay.thread.join, 12)
        if not replay.thread.is_alive():
            dispose_audit_engine()
        dispose_auth_client()
        dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="LOLPICK API",
        version="0.14.1",
        lifespan=lifespan,
        description="프론트 연결 계약 v0.14. 공개 목록은 익명 조회, 개인 API는 Google Bearer와 고정 6시간 서버 세션을 사용합니다. 시간·null·재시도 규칙은 전달용 API_GUIDE.html을 참조하세요.",
        responses={status: {"model": ErrorOut, "description": description} for status, description in {
            400: "Malformed authentication", 401: "Authentication/session rejected", 403: "Administrator/MFA required",
            404: "Resource not found", 408: "Request body deadline", 409: "State/idempotency/integrity conflict",
            413: "Request too large", 415: "JSON content type required", 422: "Invalid request",
            429: "Rate limit; honor Retry-After", 431: "Headers too large", 503: "Temporary failure; honor Retry-After",
        }.items()},
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.app_env != "production" else None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
        expose_headers=["X-Request-ID", "X-Data-Stale", "X-Data-Age-Seconds", "Retry-After"],
    )
    @application.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "invalid_request",
                    "message": "Check request field types, format and allowed values.",
                }
            },
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"code": "http_error", "message": "Request could not be processed."}
        )
        headers = {"Cache-Control": "no-store", **(exc.headers or {})}
        if exc.status_code >= 500:
            request.state.error_detail = getattr(request.state, "error_detail", None) or error_details(exc)
            detail = {"code": detail.get("code", "service_unavailable"), "message": TEMPORARY_MESSAGE}
            headers.setdefault("Retry-After", "5")
        return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=headers)

    @application.exception_handler(PointOperationError)
    async def point_error(request: Request, exc: PointOperationError):
        request.state.error_detail = error_details(exc)
        messages = {
            "point_balance_limit": "Insufficient points or point limit exceeded.",
            "season_not_active": "Betting requires the active season.",
            "betting_closed": "Betting is closed for this match.",
            "provider_data_stale": "Wait for a fresh provider update.",
            "bet_already_placed": "You have already placed a bet on this match.",
            "idempotency_conflict": "Use a new key for a different request.",
        }
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": exc.code,
                    "message": messages.get(exc.code, "Point integrity verification failed; administrator review is required."),
                }
            },
            headers={"Cache-Control": "no-store"},
        )

    @application.exception_handler(AuthError)
    async def auth_error(request: Request, exc: AuthError):
        request.state.error_detail = error_details(exc)
        headers = {"Cache-Control": "no-store"}
        if exc.status_code == 401:
            headers["WWW-Authenticate"] = "Bearer"
        if exc.status_code >= 500:
            headers["Retry-After"] = "5"
        return JSONResponse(status_code=exc.status_code,
            content={"detail": {"code": exc.code,
                "message": TEMPORARY_MESSAGE if exc.status_code >= 500 else exc.message}}, headers=headers)

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        request.state.error_detail = error_details(exc)
        # Exception messages/tracebacks can contain DB URLs. Log only the type.
        logger.error("Database request failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "detail": {"code": "database_unavailable", "message": TEMPORARY_MESSAGE}
            },
            headers={"Cache-Control": "no-store", "Retry-After": "5"},
        )

    @application.exception_handler(DatabaseConfigurationError)
    async def configuration_error(request: Request, exc: DatabaseConfigurationError):
        request.state.error_detail = error_details(exc)
        logger.error("Configuration failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "detail": {"code": "configuration_error", "message": TEMPORARY_MESSAGE}
            },
            headers={"Cache-Control": "no-store", "Retry-After": "5"},
        )

    application.include_router(router)
    application.include_router(auth_router)
    application.include_router(admin_router)
    application.include_router(user_actions_router)
    application.add_middleware(RequestEnvelopeMiddleware)
    application.add_middleware(ResourceLimitsMiddleware)
    attach_activity_middleware(application)
    snapshots = PublicSnapshots()
    application.state.public_snapshots = snapshots
    application.add_middleware(PublicAvailabilityMiddleware, store=snapshots)
    # Also cover early audit/admission/validation errors. Otherwise the browser
    # can mask their JSON as a CORS/network failure instead of a usable response.
    application.add_middleware(ResponseCORSMiddleware)
    schema = application.openapi()
    # Catalog tags are declared on every public read route; new catalog routes
    # inherit the correct optional-auth contract automatically.
    for operations in schema["paths"].values():
        for operation in operations.values():
            if isinstance(operation, dict) and "catalog" in operation.get("tags", []):
                operation["security"] = [{"HTTPBearer": []}, {}]
    return application


app = create_app()
