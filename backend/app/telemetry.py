"""Every HTTP request: bounded redacted error input, DB-independent JSON lines."""

import asyncio
import threading
import json
import math
import logging
import os
import re
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from time import perf_counter
from uuid import UUID, uuid4
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from app.core.config import get_settings
from app.db.session import get_audit_engine
from app.audit_journal import get_journal, private_directory
from app.models import Member, Season, SystemLog
from sqlalchemy import select
from hashlib import sha256
from app.db.compact import key_hash, checked_document
from app.db.schema import CODES

LIMIT = 32768
TOKEN = re.compile(r"(?i)bearer\s+\S+|(?:[A-Za-z0-9_-]{8,}\.){2}[A-Za-z0-9_-]+|sb_(?:secret|publishable)_\S+")


def sensitive(key):
    name = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return any(
        word in name
        for word in (
            "token",
            "password",
            "secret",
            "authorization",
            "cookie",
            "apikey",
            "credential",
            "databaseurl",
            "connectionstring",
            "privatekey",
            "email",
            "googlesub",
            "input",
            "msg",
            "ctx",
        )
    )


def redact(value, depth=0):
    if depth > 12:
        return "[DEPTH_LIMIT]"
    if isinstance(value, dict):
        # Pydantic validation locations reveal whether an 'input' is a secret.
        location = value.get("loc", [])
        location = location if isinstance(location, (list, tuple)) else []
        secret_input = any(sensitive(part) for part in location if isinstance(part, str))
        result = {
            str(k)[:160]: "[REDACTED]"
            if sensitive(k) or (secret_input and k in {"input", "msg", "ctx"})
            else redact(v, depth + 1)
            for k, v in list(value.items())[:200]
        }
        if len(value) > 200:
            result["$lolpick_capture"] = {"capture": "object_items_truncated", "total_items": len(value)}
        return result
    if isinstance(value, (tuple, list)):
        result = [redact(v, depth + 1) for v in value[:200]]
        if len(value) > 200:
            result.append({"capture": "array_items_truncated", "total_items": len(value)})
        return result
    if isinstance(value, str):
        return TOKEN.sub("[REDACTED]", value[:4096]) + (
            f"[TRUNCATED:total_chars={len(value)}]" if len(value) > 4096 else ""
        )
    if type(value) is float and not math.isfinite(value):
        return f"[NON_FINITE:{value}]"
    if value is None or type(value) in (int, float, bool):
        return value
    return "[UNSUPPORTED]"


def shape(value, depth=0):
    """Never store input values or untrusted field names, even unknown nested fields."""
    if depth > 6:
        return {"capture": "depth_limit"}
    if isinstance(value, dict):
        allowed = {"result", "score_a", "score_b"}
        return {
            "type": "object",
            "field_count": len(value),
            "known_fields": {k: shape(v, depth + 1) for k, v in value.items() if k in allowed},
            "unknown_field_count": len(set(value) - allowed),
        }
    if isinstance(value, list):
        return {"type": "array", "item_count": len(value)}
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    return {
        "type": "null"
        if value is None
        else "boolean"
        if type(value) is bool
        else "number"
        if type(value) in (int, float)
        else "other"
    }


def captured_json(data, complete):
    if not complete:
        return {"capture": "incomplete_or_over_limit", "limit_bytes": LIMIT}
    if not data:
        return None
    try:
        return shape(json.loads(data))
    except (ValueError, UnicodeError, RecursionError):
        return {"capture": "invalid_or_non_json_omitted", "bytes": len(data)}


class PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            return os.fdopen(fd, "a", encoding="utf-8")
        except Exception:
            os.close(fd)
            raise


def audit_logger():
    logger = logging.getLogger("lolpick.http_audit")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        logger.propagate = False
        stdout = logging.StreamHandler(sys.stdout)
        stdout.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stdout)
        try:
            directory = private_directory(get_settings().log_directory)
            # Per-process files avoid competing rotation across Uvicorn workers.
            path = directory / f"lolpick-{os.getpid()}.log"
            file = PrivateRotatingHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
            os.chmod(path, 0o600)
            file.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(file)
        except OSError:
            logger.error(
                json.dumps(
                    {"created_at": datetime.now(timezone.utc).isoformat(), "event_name": "audit.file_unavailable"}
                )
            )
    return logger


def mirror_event(event):
    try:
        getattr(audit_logger(), {"info": "info", "warning": "warning", "error": "error"}[event["severity"]])(
            json.dumps(event, ensure_ascii=False, allow_nan=False)
        )
    except Exception:
        # Mirror is secondary. Never expose a raw logging exception to the client.
        pass


def emit_event(event):
    get_journal().append(event)  # Durable authoritative copy; failure is NOT swallowed here.
    mirror_event(event)


def persist_http_event(event):
    """Idempotent database delivery on an independent, bounded connection pool."""
    try:
        engine = get_audit_engine()
        with Session(engine) as db, db.begin():
            raw_actor = event.get("actor_member_id")
            actor = None
            if raw_actor is not None:
                if type(raw_actor) is int or (isinstance(raw_actor, str) and raw_actor.isdecimal()):
                    candidate = int(raw_actor)
                    actor = candidate if db.get(Member, candidate) is not None else None
                else:
                    actor = db.scalar(select(Member.id).where(Member.auth_user_id == UUID(str(raw_actor))))
            raw_season = event.get("season_id")
            season_id = None
            if type(raw_season) is int:
                season_id = raw_season if db.get(Season, raw_season) is not None else None
            elif raw_season is not None:
                season_id = db.scalar(select(Season.id).where(Season.public_code == raw_season))
            known = {label: code for code, label in CODES["event"].items()}
            name = event["event_name"]
            context = {"response_complete": bool(event.get("response_complete", False))}
            if event.get("reason") is not None:
                context["reason"] = str(event["reason"])[:160]
            if name not in known:
                context.update(legacy_event_name=name[:160], legacy_feature=str(event.get("feature", ""))[:160])
            message = event.get("message")
            error_code = event.get('error_code') or (message.get("code") if isinstance(message, dict) else None)
            if event.get('archive_file'):
                context['archive_file'] = event['archive_file']
            detail = {key: event[key] for key in ("message", "error_detail", "input") if event.get(key) is not None}
            # The bounded DB row references the locally retained, redacted detail by digest.
            digest = (
                sha256(json.dumps(detail, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).digest()
                if detail
                else None
            )
            method = event.get("http_method")
            if method is not None and method not in CODES["http_method"].values():
                method = "OTHER"
            route = event.get("api_path")
            route_code = next((code for code, label in CODES["route"].items() if label == route), 0) if route else None
            phase = "finished" if event.get("phase") == "failed" else event.get("phase", "unknown")
            values = dict(
                event_key_hash=key_hash(event["event_key"]),
                season_id=season_id,
                actor_member_id=actor,
                event_code=known.get(name, 900),
                outcome=event["outcome"],
                severity=event["severity"],
                source=event.get("source", "api"),
                request_id=UUID(event["request_id"]),
                duration_ms=event.get("duration_ms"),
                route_code=route_code,
                http_method=method,
                status_code=event.get("status_code"),
                phase=phase,
                error_code=str(error_code)[:80] if error_code else None,
                detail_sha256=digest,
                context=checked_document(
                    context, {"response_complete", "reason", "legacy_event_name", "legacy_feature", "archive_file"}, 1024
                ),
                created_at=datetime.fromisoformat(event["created_at"]),
            )
            # Commit can succeed even if its acknowledgement is lost: deduplicate retries in DB.
            if engine.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            elif engine.dialect.name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert
            else:
                return False
            db.execute(
                insert(SystemLog.__table__)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["event_key_hash"], index_where=SystemLog.event_key_hash.is_not(None)
                )
            )
        return True  # Only after commit.
    except Exception as exc:
        mirror_event(
            dict(
                created_at=datetime.now(timezone.utc).isoformat(),
                event_name="audit.db_unavailable",
                request_id=event.get("request_id"),
                severity="error",
                error_type=type(exc).__name__,
            )
        )
        return False


class ReplayWorker:
    def __init__(self):
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="audit-replay", daemon=True)

    def run(self):
        while not self.stop.wait(1):
            try:
                if get_settings().system_log_enabled:
                    get_journal().replay(persist_http_event)
                else:
                    get_journal().archive_pending()
            except Exception:
                mirror_event(
                    {
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "event_name": "audit.replay_unavailable",
                        "severity": "error",
                    }
                )


class ResponseStreamError(RuntimeError):
    """Sanitized exception: server closes the stream without printing original secrets."""


class AuditMiddleware:
    def __init__(self, app):
        self.app, self.inflight = app, 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # Bound admission tasks too, before the first threadpool/journal wait.
        if self.inflight >= get_settings().request_max_concurrent * 2:
            request_id = str(uuid4())
            mirror_event(
                {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "event_name": "http.admission_rejected",
                    "request_id": request_id,
                    "severity": "warning",
                    "reason": "server_busy",
                }
            )
            return await JSONResponse(
                status_code=503,
                content={"detail": {"code": "server_busy", "message": "Try again later."}},
                headers={"X-Request-ID": request_id, "Retry-After": "1"},
            )(scope, receive, send)
        self.inflight += 1
        try:
            return await self.process(scope, receive, send)
        finally:
            self.inflight -= 1

    async def process(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id, started = uuid4(), perf_counter()
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        method = (
            scope["method"]
            if scope["method"] in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE", "CONNECT"}
            else "OTHER"
        )
        event = dict(
            created_at=datetime.now(timezone.utc).isoformat(),
            request_id=str(request_id),
            event_key=f"http:{request_id}:start",
            event_name="http.request.started",
            phase="started",
            api_path="[unmatched]",
            feature="http",
            http_method=method,
            outcome="success",
            severity="info",
            duration_ms=0,
            response_complete=False,
        )
        try:
            await run_in_threadpool(emit_event, event)
        except Exception:
            mirror_event(
                {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "event_name": "audit.start_unavailable",
                    "request_id": str(request_id),
                    "severity": "error",
                }
            )
            await JSONResponse(
                status_code=503,
                content={
                    "detail": {
                        "code": "audit_unavailable",
                        "message": "잠시 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                    }
                },
                headers={"X-Request-ID": str(request_id)},
            )(scope, receive, send)
            return  # Fail closed BEFORE business processing.
        captured, error_body = bytearray(), bytearray()
        complete, response_complete, response_started, status, error_type = False, False, False, 500, None
        overflow, reason = False, None

        async def monitored_receive():
            nonlocal complete, overflow, reason
            message = await receive()
            if message["type"] == "http.disconnect":
                reason = "client_disconnected"
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                remaining = max(0, LIMIT - len(captured))
                captured.extend(chunk[:remaining])
                overflow = overflow or len(chunk) > remaining
                complete = not message.get("more_body", False) and not overflow
            return message

        async def monitored_send(message):
            nonlocal status, response_complete, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + [(b"x-request-id", str(request_id).encode())]
                response_started = True
            elif message["type"] == "http.response.body" and status >= 400:
                error_body.extend(message.get("body", b"")[: max(0, LIMIT - len(error_body))])
            await send(message)
            if message["type"] == "http.response.body":
                response_complete = not message.get("more_body", False)

        try:
            await self.app(scope, monitored_receive, monitored_send)
        except asyncio.CancelledError:
            error_type, reason = "CancelledError", "request_cancelled"
            raise
        except Exception as exc:
            error_type, reason = type(exc).__name__, "handler_exception"
            from app.core.error_details import error_details

            state["error_detail"] = error_details(exc)
            if not response_started:
                await JSONResponse(
                    status_code=500,
                    content={
                        "detail": {"code": "internal_error", "message": "잠시 처리할 수 없습니다. 잠시 후 다시 시도해 주세요."}
                    },
                )(scope, receive, monitored_send)
            else:
                raise ResponseStreamError("Streaming response failed; see X-Request-ID.") from None
        finally:
            try:
                route = scope.get("route")
                from app.audit_input import audit_route, captured_input, response_error_code
                path = getattr(route, "path", None) or audit_route(scope)
                category = {"/api/v1/teams": "teams", "/api/v1/seasons": "seasons", "/api/v1/events": "seasons"}
                feature = category.get(
                    path, "matches" if "/matches" in path else "auth" if path.startswith("/api/v1/auth") else "http"
                )
                failed = bool(error_type) or not response_complete or status >= 500
                code = response_error_code(error_body, state.get('error_detail')) if status >= 400 or failed else None
                event.update(
                    created_at=datetime.now(timezone.utc).isoformat(),
                    event_key=f"http:{request_id}:finish",
                    event_name="http.request",
                    phase="finished",
                    actor_member_id=str(state["actor_member_id"]) if state.get("actor_member_id") else None,
                    season_id=state.get("log_season_id"),
                    api_path=path,
                    feature=feature,
                    status_code=status,
                    response_complete=response_complete,
                    reason=reason or ("response_incomplete" if not response_complete else code or
                                      ("request_rejected" if status >= 400 else "completed")),
                    duration_ms=max(0, int((perf_counter() - started) * 1000)),
                    outcome="failure" if failed else "rejected" if status >= 400 else "success",
                    severity="error" if failed else "warning" if status >= 400 else "info",
                )
                if status >= 400 or failed:
                    event['error_code'] = code
                    event["message"] = {
                        "input": {
                            "query_bytes": len(scope.get("query_string", b"")),
                            "path_parameter_count": len(scope.get("path_params", {})),
                            "body": captured_input(captured, complete, state),
                            "safe_parameters": state.get('audit_safe_parameters', {}),
                        },
                        "error": {"code": code},
                        "error_type": error_type,
                        "server_error": state.get("error_detail"),
                    }
                # Synchronous bounded local commit survives cooperative cancellation.
                # No database/network wait occurs in the request's finalizer.
                if state.get("public_response_buffered"):
                    # The outer public boundary has not sent these bytes yet.
                    # It records this terminal only after the actual ASGI send.
                    state["buffered_audit_terminal"] = event
                else:
                    emit_event(event)
            except Exception:
                # Start entry remains completion_unknown, not falsely successful.
                mirror_event(
                    {
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "event_name": "audit.finish_unavailable",
                        "request_id": str(request_id),
                        "severity": "error",
                    }
                )


def attach_activity_middleware(application):
    application.add_middleware(AuditMiddleware)
