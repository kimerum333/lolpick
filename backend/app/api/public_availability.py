"""Bounded last-known PUBLIC JSON. Never substitute authentication or writes."""
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import os
from time import monotonic
from uuid import uuid4

from starlette.responses import JSONResponse, Response
from starlette.datastructures import MutableHeaders
from app.core.config import get_settings
from app.api.resource_limits import request_client

CATALOG = {"/api/v1/teams", "/api/v1/seasons", "/api/v1/events", "/api/v1/matches"}
MAX_BODY = 262144
MAX_TOTAL = 8 * 1024 * 1024
MAX_ENTRIES = 128
TEMPORARY_MESSAGE = "잠시 처리할 수 없습니다. 잠시 후 다시 시도해 주세요."


class ResponseCORSMiddleware:
    """Cover early errors without intercepting/auditing preflight twice."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        origins = [v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"origin"]
        allowed = len(origins) == 1 and origins[0] in get_settings().cors_origins
        async def cors_send(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", []))}
                headers = MutableHeaders(scope=message)
                if (scope.get('path','').startswith(('/api/v1/auth/','/api/v1/admin/'))
                        or any(k in {b'authorization', b'cookie'} for k, _ in scope.get('headers', []))):
                    headers['Cache-Control'] = 'no-store'
                headers['X-Content-Type-Options'] = 'nosniff'
                if allowed:
                    headers["Access-Control-Allow-Origin"] = origins[0]
                    headers["Access-Control-Expose-Headers"] = "X-Request-ID, X-Data-Stale, X-Data-Age-Seconds, Retry-After"
                    headers.add_vary_header("Origin")
            await send(message)
        await self.app(scope, receive, cors_send)


def catalog_path(path):
    if path in CATALOG:
        return True
    for prefix in ("/api/v1/matches/", "/api/v1/events/"):
        if not path.startswith(prefix):
            continue
        suffix = path[len(prefix):]
        if prefix.endswith("matches/") and suffix.endswith("/stake-ranking"):
            suffix = suffix.removesuffix("/stake-ranking")
        return 1 <= len(suffix) <= 80 and "/" not in suffix and all(ord(c) >= 32 for c in suffix)
    return False



def anonymous_read(scope):
    headers = scope.get("headers", [])
    return (scope.get("method") == "GET"
            and not any(k in {b"authorization", b"cookie", b"transfer-encoding"} for k, _ in headers)
            and all(v == b"0" for k, v in headers if k == b"content-length")
            and len(headers) <= 128 and sum(len(k) + len(v) for k, v in headers) <= 16384
            and len(scope.get("query_string", b"")) <= 32768)


def closed_betting(value):
    if isinstance(value, dict):
        if "bettingOpen" in value:
            value["bettingOpen"] = False
            value["bettingClosedReason"] = "cached_snapshot"
            issues = value.get("dataIssues")
            if isinstance(issues, list) and "cached_snapshot" not in issues:
                issues.append("cached_snapshot")
        for child in value.values():
            closed_betting(child)
    elif isinstance(value, list):
        for child in value:
            closed_betting(child)


@dataclass(frozen=True)
class Snapshot:
    body: bytes
    created: float


class PublicSnapshots:
    """Per-worker, non-persistent and bounded. All calls run on its ASGI loop."""
    def __init__(self, *, clock=monotonic):
        self.clock = clock
        self.entries = OrderedDict()
        self.bytes = 0
        self.circuit_until = 0.0
        self.buckets = {}
        self.minute = -1
        self.secret = os.urandom(32)

    def put(self, key, body):
        if len(body) > MAX_BODY:
            return
        try:
            value = json.loads(body)
            if not isinstance(value, (dict, list)):
                return
            closed_betting(value)
            body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        except (ValueError, UnicodeError, RecursionError):
            return
        if len(body) > MAX_BODY:
            return
        previous = self.entries.pop(key, None)
        if previous:
            self.bytes -= len(previous.body)
        self.entries[key] = Snapshot(body, self.clock())
        self.bytes += len(body)
        while len(self.entries) > MAX_ENTRIES or self.bytes > MAX_TOTAL:
            _, old = self.entries.popitem(last=False)
            self.bytes -= len(old.body)

    def get(self, key):
        row = self.entries.get(key)
        if row is None:
            return None
        if self.clock() - row.created > get_settings().public_snapshot_max_age_seconds:
            self.bytes -= len(self.entries.pop(key).body)
            return None
        self.entries.move_to_end(key)
        return row

    def admit(self, scope):
        minute = int(self.clock() // 60)
        if minute != self.minute:
            self.buckets.clear()
            self.minute = minute
        client = request_client(scope, get_settings())
        bucket = hmac.new(self.secret, client.encode(), sha256).digest()
        if bucket not in self.buckets and len(self.buckets) >= 10000:
            return False
        hits = self.buckets.get(bucket, 0)
        if hits >= get_settings().rate_public_per_minute:
            return False
        self.buckets[bucket] = hits + 1
        return True

    def clear(self):
        self.entries.clear()
        self.bytes = 0
        self.circuit_until = 0.0


class PublicAvailabilityMiddleware:
    def __init__(self, app, store):
        self.app, self.store = app, store

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        state = scope.setdefault("state", {})
        delivered, delivered_status = False, 503
        async def track_delivery(message):
            nonlocal delivered, delivered_status
            await send(message)
            if message["type"] == "http.response.start":
                delivered_status = message["status"]
            if message["type"] == "http.response.body":
                delivered = not message.get("more_body", False)
        try:
            await self.process(scope, receive, track_delivery)
        finally:
            terminal = state.pop("buffered_audit_terminal", None)
            if terminal is not None:
                from app.telemetry import emit_event, mirror_event
                terminal.update(status_code=delivered_status, response_complete=delivered)
                if state.get("public_snapshot_used"):
                    terminal["reason"] = "cached_snapshot"
                if not delivered:
                    terminal.update(outcome="failure", severity="error", reason="response_incomplete")
                try:
                    emit_event(terminal)
                except Exception:
                    mirror_event({"event_name": "audit.finish_unavailable", "severity": "error",
                                  "request_id": terminal["request_id"], "created_at": terminal["created_at"]})

    async def process(self, scope, receive, send):
        path = scope.get("path", "")
        candidate = (scope["type"] == "http" and anonymous_read(scope)
                     and (catalog_path(path) or path == "/api/v1/health"))
        if not candidate:
            async def invalidate(message):
                # Successful API writes invalidate old public totals/results.
                if (scope.get("method") == "POST" and path.startswith("/api/v1/")
                        and message["type"] == "http.response.start" and 200 <= message["status"] < 300):
                    self.store.clear()
                await send(message)
            return await self.app(scope, receive, invalidate)
        key = (path, scope.get("query_string", b""))
        if catalog_path(path) and self.store.clock() < self.store.circuit_until:
            row = self.store.get(key)
            if row is not None:
                return await self.fallback(scope, receive, send, row)
        scope["state"]["public_response_buffered"] = True
        messages, size, passthrough = [], 0, False

        async def buffer(message):
            nonlocal size, passthrough
            size += len(message.get("body", b""))
            if not passthrough and size <= MAX_BODY:
                messages.append(message)
                return
            if not passthrough:
                for previous in messages:
                    await send(previous)
                messages.clear()
                passthrough = True
                scope["state"]["public_response_buffered"] = False
            await send(message)

        failed = False
        try:
            await self.app(scope, receive, buffer)
        except Exception:
            if passthrough:
                raise
            failed = True
        if passthrough:
            return
        start = next((m for m in messages if m["type"] == "http.response.start"), None)
        bodies = [m for m in messages if m["type"] == "http.response.body"]
        complete = bool(bodies) and not bodies[-1].get("more_body", False)
        status = start["status"] if start else 503
        if failed or not complete or status in {500, 502, 503, 504}:
            row = self.store.get(key) if catalog_path(path) else None
            if row is not None or (path == "/api/v1/health" and not scope.get("query_string")):
                self.store.circuit_until = self.store.clock() + get_settings().public_circuit_seconds
                return await self.fallback(scope, receive, send, row)
            if failed or not complete:
                return await JSONResponse(status_code=503, content={"detail": {"code": "service_unavailable",
                    "message": TEMPORARY_MESSAGE}}, headers=self.headers(scope))(scope, receive, send)
        if status == 200 and complete and catalog_path(path):
            route = scope.get("route")
            if getattr(route, "path", None) in CATALOG | {"/api/v1/matches/{match_id}"}:
                self.store.put(key, b"".join(m.get("body", b"") for m in bodies))
        for message in messages:
            await send(message)

    def headers(self, scope):
        request_id = str(scope.get("state", {}).get("request_id") or uuid4())
        headers = {"Cache-Control": "no-store", "X-Request-ID": request_id}
        origins = [v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"origin"]
        if len(origins) == 1 and origins[0] in get_settings().cors_origins:
            headers.update({"Access-Control-Allow-Origin": origins[0], "Vary": "Origin",
                            "Access-Control-Expose-Headers": "X-Request-ID, X-Data-Stale, X-Data-Age-Seconds, Retry-After"})
        return headers

    async def fallback(self, scope, receive, send, row):
        from datetime import datetime, timezone
        from app.telemetry import mirror_event
        headers = self.headers(scope)
        if not self.store.admit(scope):
            headers["Retry-After"] = "60"
            return await JSONResponse(status_code=429, content={"detail": {"code": "rate_limited", "message":
                "잠시 후 다시 시도해 주세요."}}, headers=headers)(scope, receive, send)
        mirror_event({"created_at": datetime.now(timezone.utc).isoformat(),
                      "event_name": "http.public_fallback", "severity": "warning",
                      "request_id": headers["X-Request-ID"], "cached": row is not None})
        if row is None:
            return await JSONResponse({"status": "ok", "service": "lolpick-backend", "version": "0.13.0"},
                                      headers=headers)(scope, receive, send)
        scope["state"]["public_snapshot_used"] = True
        headers.update({"X-Data-Stale": "true", "X-Data-Age-Seconds": str(max(0, int(self.store.clock()-row.created)))})
        await Response(row.body, media_type="application/json", headers=headers)(scope, receive, send)
