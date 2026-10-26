"""Common pre-handler validation for every application API, including no-input APIs."""
import asyncio
import json
import math
import re
from fastapi import HTTPException, Request
from pydantic import ValidationError
from starlette.responses import JSONResponse
from app.schemas.results import MatchResultRequest
from app.schemas.user_actions import BetRequest, CheerRequest
from app.core.config import get_settings

LIMIT = 32768


def reject(message: str, status: int = 422):
    raise HTTPException(status, detail={"code": "invalid_request", "message": message})


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError("Non-finite JSON number.")


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number.")
    return number


def decode_object(body):
    data = json.loads(body, object_pairs_hook=object_pairs, parse_constant=invalid_constant,
                      parse_float=finite_float)
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")
    return data


class RequestEnvelopeMiddleware:
    """Bound and decode input before FastAPI attempts its own body parsing."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/v1/") or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return
        try:
            if len(scope.get("query_string", b"")) > LIMIT:
                reject("Query string exceeds 32768 bytes.", 413)
            headers = {}
            for key, value in scope.get("headers", []):
                if key in (b"content-length", b"content-type", b"authorization", b"idempotency-key") and key in headers:
                    reject("Duplicate request header.")
                headers[key] = value
            length = headers.get(b"content-length")
            if length is not None:
                if not length.isdigit():
                    reject("Invalid Content-Length.")
                if len(length) > 10 or int(length) > LIMIT:
                    reject("Request body exceeds 32768 bytes.", 413)
            body = bytearray()
            more = True
            try:
                async with asyncio.timeout(get_settings().request_body_timeout):
                    while more:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > LIMIT:
                            reject("Request body exceeds 32768 bytes.", 413)
                        body.extend(chunk)
                        more = message.get("more_body", False)
            except TimeoutError:
                reject("Request body read deadline exceeded.", 408)
            if length is not None and len(body) != int(length):
                reject("Content-Length does not match request bytes.")
            if body:
                content_type = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
                if content_type != b"application/json":
                    reject("Use application/json for request bodies.", 415)
                try:
                    scope.setdefault("state", {})["validated_json"] = decode_object(body)
                except (ValueError, UnicodeError, RecursionError):
                    reject("Malformed JSON object request.")
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

        except HTTPException as exc:
            await JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})(scope, receive, send)
            return
        await self.app(scope, replay, send)


async def validate_api_request(request: Request):
    route = request.scope["route"]
    allowed = {field.alias for field in route.dependant.query_params}
    seen = set()
    for key, value in request.query_params.multi_items():
        if key not in allowed or key in seen:
            reject("Unknown or duplicate query parameter.")
        seen.add(key)
        if key == "date" and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            reject("Use date in YYYY-MM-DD format.")
        if not value or value != value.strip() or len(value) > 160 or any(ord(c) < 32 for c in value):
            reject("Query parameters must contain a bounded, nonblank value.")
    for value in request.path_params.values():
        if not isinstance(value, str) or not 1 <= len(value) <= 80 or value != value.strip() or any(ord(c) < 32 for c in value):
            reject("Invalid path identifier.")
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdigit():
            reject("Invalid Content-Length.")
        if len(length) > 10 or int(length) > LIMIT:
            reject("Request body exceeds 32768 bytes.", 413)
    # Stream-bounded even when Content-Length is absent or inaccurate.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > LIMIT:
            reject("Request body exceeds 32768 bytes.", 413)
        body.extend(chunk)
    request._body = bytes(body)  # Cache for FastAPI's typed body decoder.
    body_model = {
        "/api/v1/admin/matches/{match_id}/result": MatchResultRequest,
        "/api/v1/auth/bets": BetRequest,
        "/api/v1/auth/cheer": CheerRequest,
    }.get(route.path) if request.method == "POST" else None
    if not body:
        if body_model:
            reject("A JSON request object is required.")
        return
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        reject("Use application/json for request bodies.", 415)
    try:
        data = getattr(request.state, "validated_json", None)
        if data is None:
            data = decode_object(body)
    except (ValueError, UnicodeError, RecursionError):
        reject("Malformed JSON request.")
    if not isinstance(data, dict):
        reject("Request body must be a JSON object.")
    if body_model:
        try:
            validated = body_model.model_validate(data)
            if body_model is MatchResultRequest:
                request.state.validated_result = validated
        except ValidationError:
            reject("Invalid field types, values or unexpected request fields.")
    elif data or request.method == "GET":
        reject("This API does not accept request body fields.")
