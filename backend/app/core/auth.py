"""Validate bearer tokens with the configured Supabase Auth server.

Never decode an unverified JWT or trust caller-supplied member IDs/metadata.
No access/refresh tokens or provider profiles are stored in application tables.
"""
import logging
import re
import base64
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.integrations.http_json import read_json

logger = logging.getLogger("lolpick.auth")
bearer = HTTPBearer(auto_error=False)
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")


class AuthError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(code)


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: UUID
    google_sub: str = field(repr=False)
    session_id: UUID | None = None
    oauth_at: datetime | None = None
    aal: str = 'aal1'


@lru_cache
def get_auth_client() -> httpx.Client:
    settings = get_settings()
    url, key = settings.auth_configuration()
    return httpx.Client(
        base_url=url, headers={"apikey": key, "Accept-Encoding": "identity"}, timeout=settings.supabase_auth_timeout,
        follow_redirects=False, trust_env=False,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
    )


def dispose_auth_client():
    if get_auth_client.cache_info().currsize:
        get_auth_client().close()
        get_auth_client.cache_clear()


def invalid_token() -> AuthError:
    return AuthError(401, "invalid_token", "The access token is invalid or expired.")


def unavailable() -> AuthError:
    return AuthError(503, "auth_unavailable", "Authentication service is temporarily unavailable.")


def verified_user(payload: object) -> AuthenticatedUser:
    if not isinstance(payload, dict):
        raise unavailable()
    try:
        user_id = UUID(payload["id"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise unavailable() from None
    if payload.get("is_anonymous") is True or payload.get("deleted_at"):
        raise invalid_token()
    if payload.get("banned_until"):
        try:
            until = datetime.fromisoformat(payload["banned_until"].replace("Z", "+00:00"))
            if until.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise unavailable() from None
        if until > datetime.now(timezone.utc):
            raise AuthError(403, "account_unavailable", "This account cannot access the service.")
    identities = payload.get("identities")
    if not isinstance(identities, list):
        raise unavailable()
    google = [row for row in identities if isinstance(row, dict) and row.get("provider") == "google"]
    if len(google) != 1:
        raise AuthError(403, "google_identity_required", "A Google identity is required.")
    if len(identities) != 1:
        raise AuthError(403, "google_only_required", "Only Google-only accounts can access this service.")
    identity = google[0]
    data = identity.get("identity_data")
    subject = data.get("sub") if isinstance(data, dict) else None
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise unavailable()
    if identity.get("provider_id") is not None and identity["provider_id"] != subject:
        raise unavailable()
    if identity.get("user_id") is not None and identity["user_id"] != str(user_id):
        raise unavailable()
    # user_metadata is editable by users: never use it as a Google identity.
    return AuthenticatedUser(user_id=user_id, google_sub=subject)


def verify_access_token(token: str, *, client: httpx.Client | None = None) -> AuthenticatedUser:
    if not isinstance(token, str) or len(token) > 8192 or not TOKEN_PATTERN.fullmatch(token):
        raise invalid_token()
    client = client if client is not None else get_auth_client()
    try:
        with client.stream("GET", "/auth/v1/user", headers={"Authorization": f"Bearer {token}",
                          "Accept-Encoding": "identity"}) as response:
            if response.status_code in (400, 401, 403, 422):
                raise invalid_token()
            if response.status_code != 200:
                raise unavailable()
            try:
                payload = read_json(response, 262144)
            except ValueError:
                raise unavailable() from None
    except httpx.HTTPError as exc:
        logger.warning("Auth service request failed: %s", type(exc).__name__)
        raise unavailable() from None
    user = verified_user(payload)
    # /auth/v1/user has validated this exact token; only now read its claims.
    try:
        segment = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        now = datetime.now(timezone.utc)
        methods = claims["amr"]
        if not isinstance(methods, list) or not methods or any(
                not isinstance(m, dict) or not isinstance(m.get("method"), str) or
                type(m.get("timestamp")) is not int or m["timestamp"] < 0 for m in methods):
            raise ValueError()
        oauth = [m for m in methods if m["method"] == "oauth"]
        aal = claims.get('aal', 'aal1')
        secondary = [m for m in methods if m['method'] == 'totp']
        issuer = str(client.base_url).rstrip("/") + "/auth/v1"
        audience = claims["aud"]
        if (claims["sub"] != str(user.user_id) or claims["iss"] != issuer
                or claims["role"] != "authenticated" or claims.get("is_anonymous") is True
                or not (audience == "authenticated" or audience == ["authenticated"])
                or type(claims["exp"]) is not int or claims["exp"] <= now.timestamp()
                or aal not in {'aal1','aal2'} or (secondary and aal != 'aal2')
                or (aal == 'aal2' and not secondary)
                or not oauth or any(m["method"] not in {"oauth", "token_refresh", 'totp'} for m in methods)
                or any(m['timestamp'] > now.timestamp() for m in methods)):
            raise ValueError()
        session_id = UUID(claims["session_id"])
        oauth_at = datetime.fromtimestamp(max(m["timestamp"] for m in oauth), timezone.utc)
        if oauth_at > now:
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError):
        raise AuthError(401, "google_login_required", "Perform a new Google OAuth login.") from None
    return replace(user, session_id=session_id, oauth_at=oauth_at, aal=aal)


def get_authenticated_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthError(401, "authentication_required", "Provide a Supabase access token in the Authorization header.")
    return verify_access_token(credentials.credentials)
