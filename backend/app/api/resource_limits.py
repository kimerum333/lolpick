"""Bound inflight requests; shared host-level rate limits without storing raw IPs."""
import ipaddress
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from fastapi import HTTPException
from app.audit_journal import get_journal
from app.core.config import get_settings


def request_client(scope, settings):
    peer = scope.get("client", ("unknown", 0)) or ("unknown", 0)
    client = peer[0]
    try:
        address = ipaddress.ip_address(client)
        trusted = any(address in ipaddress.ip_network(n) for n in settings.trusted_proxy_cidrs)
        forwarded = [v for k, v in scope.get("headers", []) if k == b"x-real-ip"]
        if trusted and len(forwarded) == 1:
            client = str(ipaddress.ip_address(forwarded[0].decode("ascii")))
    except (ValueError, UnicodeError):
        pass
    return client


def enforce_identity_limits(request, user):
    if getattr(request.state, 'identity_limited', False):
        return
    settings = get_settings()
    route = getattr(request.scope.get('route'), 'path', '[unmatched]')
    limit = settings.rate_admin_per_minute if route.startswith('/api/v1/admin/') else (
        settings.rate_auth_per_minute if route.startswith('/api/v1/auth/') else settings.rate_public_per_minute)
    entries = [(str(user.user_id), 'member', settings.rate_member_per_minute),
               (str(user.session_id), 'session', settings.rate_session_per_minute),
               (str(user.user_id), 'endpoint:'+request.method+':'+route, limit)]
    try:
        admitted = get_journal().admit_many(entries)
    except Exception:
        raise HTTPException(503, detail={'code':'audit_unavailable','message':'Try again later.'}) from None
    if not admitted:
        raise HTTPException(429,detail={'code':'rate_limited','message':'Try again later.'},
                            headers={'Retry-After':'60'})
    request.state.identity_limited = True


class ResourceLimitsMiddleware:
    def __init__(self, app):
        self.app, self.inflight = app, 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        if len(headers) > 128 or sum(len(k) + len(v) for k, v in headers) > 16384:
            return await self.reject(scope, receive, send, 431, "headers_too_large")
        settings = get_settings()
        if self.inflight >= settings.request_max_concurrent:
            return await self.reject(scope, receive, send, 503, "server_busy")
        self.inflight += 1
        try:
            client = request_client(scope, settings)
            path = scope["path"]
            category, limit = ("auth", settings.rate_auth_ip_per_minute) if path.startswith("/api/v1/auth/") else (
                ("admin", settings.rate_admin_ip_per_minute) if path.startswith("/api/v1/admin/") else
                ("public", settings.rate_public_per_minute))
            try:
                admitted = await run_in_threadpool(get_journal().admit, client, category, limit)
            except Exception:
                return await self.reject(scope, receive, send, 503, "audit_unavailable")
            if not admitted:
                return await self.reject(scope, receive, send, 429, "rate_limited")
            await self.app(scope, receive, send)
        finally:
            self.inflight -= 1

    async def reject(self, scope, receive, send, status, code):
        await JSONResponse(status_code=status, content={"detail": {"code": code, "message": "Try again later."}},
                           headers={"Retry-After": "60" if status == 429 else "1"})(scope, receive, send)
