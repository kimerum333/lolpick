from compact_fixtures import internal, lookup
from app.models import Season, Member, MemberSession
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import httpx
import base64
import json
import sys
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from app.core import auth
from app.core.auth import AuthError, AuthenticatedUser, verify_access_token
from app.core.config import Settings
from app.models import PointTransaction, SystemLog
from app.services.members import synchronize_member
from app.services.points import activate_season, apply_points

TOKEN = "header.payload.signature"


def provider_user(user_id=None, subject="112233445566778899"):
    user_id = user_id or uuid4()
    return {
        "id": str(user_id),
        "is_anonymous": False,
        "email": "must-not-be-stored@example.com",
        "user_metadata": {"sub": "untrusted-editable-value", "name": "Private Name"},
        "identities": [
            {
                "provider": "google",
                "provider_id": subject,
                "user_id": str(user_id),
                "identity_data": {"sub": subject, "email": "must-not-be-stored@example.com"},
            }
        ],
    }


@pytest.fixture
def authenticated(monkeypatch):
    payload = provider_user()
    now = datetime.now(timezone.utc)
    claims = dict(
        sub=payload["id"],
        iss="https://project.supabase.co/auth/v1",
        aud="authenticated",
        role="authenticated",
        exp=int((now + timedelta(hours=1)).timestamp()),
        session_id=str(uuid4()),
        amr=[{"method": "oauth", "timestamp": int(now.timestamp())}],
    )
    token = "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"
    monkeypatch.setattr(sys.modules[__name__], "TOKEN", token)
    from app.services import sessions

    monkeypatch.setattr(sessions, "native_session_created", lambda db, user: now)
    calls = []

    def handler(request):
        calls.append(request)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"message": "private upstream error"})
        return httpx.Response(200, json=payload)

    client = httpx.Client(
        base_url="https://project.supabase.co",
        headers={"apikey": "sb_publishable_test"},
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(auth, "verify_access_token", lambda token: verify_access_token(token, client=client))
    yield {
        "payload": payload,
        "calls": calls,
        "headers": {"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": str(uuid4())},
    }
    client.close()


def create_season(db, *, initial=100):
    db.add(
        Season(
            public_code="s1",
            slug="worlds",
            year=2026,
            name="Worlds",
            label="Worlds",
            status="active",
            initial_points=initial,
        )
    )
    db.commit()


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_first_login_uses_verified_google_identity_and_opens_season_once(client, db, authenticated):
    create_season(db)
    headers = authenticated["headers"]
    result = client.post(
        "/api/v1/auth/session",
        headers=headers,
        json={"memberId": str(uuid4()), "currentPoints": 999999, "google_sub": "forged"},
    )
    assert result.status_code == 422
    assert count(db, Member) == 0
    db.commit()
    result = client.post("/api/v1/auth/session", headers=headers)
    assert result.status_code == 200
    expected = {"memberId": authenticated["payload"]["id"], "currentPoints": 100, "currentSeasonId": "s1"}
    assert {key:result.json()[key] for key in expected} == expected
    assert result.json()["availableSeasonIds"] == ["s1"]
    assert result.json()["sessionExpiresAt"] is not None
    assert result.headers["Cache-Control"] == "no-store"
    member = lookup(db, Member, auth.verified_user(authenticated["payload"]).user_id)
    assert member.google_sub == "112233445566778899"
    assert set(Member.__table__.columns.keys()) == {
        "id",
        "auth_user_id",
        "google_sub",
        "type",
        "current_points",
        "current_season_id",
        "created_at",
        "updated_at",
    }
    assert count(db, PointTransaction) == 1 and count(db, SystemLog) == 2
    db.commit()
    assert client.post("/api/v1/auth/session", headers=headers).json() == result.json()
    assert count(db, PointTransaction) == 1 and count(db, SystemLog) == 2
    assert len(authenticated["calls"]) == 2
    request = authenticated["calls"][0]
    assert str(request.url) == "https://project.supabase.co/auth/v1/user"
    assert request.headers["apikey"] == "sb_publishable_test"


def test_relogin_does_not_reset_changed_balance_and_me_is_read_only(client, db, authenticated):
    create_season(db)
    headers = authenticated["headers"]
    assert client.post("/api/v1/auth/session", headers=headers).status_code == 200
    with db.begin():
        apply_points(
            db,
            member_id=internal(db, Member, auth.verified_user(authenticated["payload"]).user_id),
            season_id=internal(db, Season, "s1"),
            amount=30,
            reason="reward",
            idempotency_key="reward-1",
        )
    headers = {**headers, "Idempotency-Key": str(uuid4())}
    assert client.post("/api/v1/auth/session", headers=headers).json()["currentPoints"] == 130
    before = count(db, SystemLog)
    db.commit()
    result = client.get("/api/v1/auth/me", headers=headers)
    assert result.status_code == 200 and result.json()["currentPoints"] == 130
    assert result.headers["Cache-Control"] == "no-store"
    assert count(db, PointTransaction) == 2 and count(db, SystemLog) == before


def test_me_does_not_register_member(client, db, authenticated):
    response = client.get("/api/v1/auth/me", headers=authenticated["headers"])
    assert response.status_code == 404 and response.json()["detail"]["code"] == "member_not_registered"
    assert count(db, Member) == count(db, PointTransaction) == count(db, SystemLog) == 0


def test_no_active_season_and_later_activation(client, db, authenticated):
    headers = authenticated["headers"]
    first = client.post("/api/v1/auth/session", headers=headers)
    assert first.json()["currentPoints"] == 0 and first.json()["currentSeasonId"] is None
    assert count(db, PointTransaction) == 0
    db.commit()
    with db.begin():
        db.add(
            Season(
                public_code="s1",
                slug="worlds",
                year=2026,
                name="Worlds",
                label="Worlds",
                status="upcoming",
                initial_points=20,
            )
        )
        db.flush()
        assert activate_season(db, "s1") == 1
    assert client.post("/api/v1/auth/session", headers=headers).json()["currentPoints"] == 20
    assert count(db, PointTransaction) == 1
    assert count(db, SystemLog) == 3


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic credentials"}, {"Authorization": "Bearer not-a-jwt"}])
def test_missing_or_malformed_credentials_rejected_without_auth_network(client, db, authenticated, headers):
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert not authenticated["calls"]
    assert count(db, Member) == 0


def test_expired_token_does_not_touch_database(client, db, authenticated):
    response = client.post(
        "/api/v1/auth/session",
        headers={"Authorization": "Bearer expired.token.signature", "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 401
    assert "private upstream" not in response.text and "expired.token" not in response.text
    assert count(db, Member) == count(db, PointTransaction) == count(db, SystemLog) == 0


@pytest.mark.parametrize("key", [None, "not-a-uuid"])
def test_operation_uuid_required_before_member_registration(client, db, authenticated, key):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    assert client.post("/api/v1/auth/session", headers=headers).status_code == 422
    assert count(db, Member) == 0


@pytest.mark.parametrize("conflict", ["google", "uuid"])
def test_identity_conflicts_preserve_existing_member(client, db, authenticated, conflict):
    user = auth.verified_user(authenticated["payload"])
    member_id = uuid4() if conflict == "google" else user.user_id
    subject = user.google_sub if conflict == "google" else "different-google-account"
    db.add(Member(auth_user_id=member_id, google_sub=subject))
    db.commit()
    result = client.post("/api/v1/auth/session", headers=authenticated["headers"])
    assert result.status_code == 409 and result.json()["detail"]["code"] == "member_identity_conflict"
    assert count(db, Member) == 1
    assert lookup(db, Member, member_id).google_sub == subject
    assert count(db, PointTransaction) == count(db, SystemLog) == 0


def test_existing_season_balance_is_not_silently_reset(client, db, authenticated):
    user = auth.verified_user(authenticated["payload"])
    db.add_all(
        [
            Season(public_code="old", slug="worlds", year=2025, name="Old", label="Old", status="finished"),
            Season(
                public_code="new", slug="worlds", year=2026, name="New", label="New", status="active", initial_points=20
            ),
        ]
    )
    db.flush()
    db.add(
        Member(
            auth_user_id=user.user_id,
            google_sub=user.google_sub,
            current_points=50,
            current_season_id=internal(db, Season, "old"),
        )
    )
    db.commit()
    response = client.post("/api/v1/auth/session", headers=authenticated["headers"])
    assert response.status_code == 409 and response.json()["detail"]["code"] == "ledger_opening_mismatch"
    assert lookup(db, Member, user.user_id).current_points == 50
    assert count(db, PointTransaction) == count(db, SystemLog) == 0


def test_login_opens_all_active_seasons_once_without_choosing_or_extending(client, db, authenticated):
    create_season(db)
    db.add(Season(public_code="s2", slug="other", year=2026, name="Other", status="active", initial_points=20))
    db.commit()
    headers = authenticated["headers"]
    first = client.post("/api/v1/auth/session", headers=headers)
    assert first.status_code == 200 and first.json()["currentSeasonId"] is None
    assert set(first.json()["availableSeasonIds"]) == {"s1","s2"}
    assert count(db, PointTransaction) == 2
    db.commit()
    second = client.post("/api/v1/auth/session", headers=headers)
    assert second.json() == first.json() and count(db, PointTransaction) == 2
    member = lookup(db, Member, auth.verified_user(authenticated["payload"]).user_id)
    db.commit()
    from app.services.points import season_balance
    assert season_balance(db, member, lookup(db, Season, "s1")) == 100
    assert season_balance(db, member, lookup(db, Season, "s2")) == 20
    assert all(row.season_id is None for row in db.scalars(select(SystemLog).where(
        SystemLog.event_name.in_(["auth.member.created","auth.session.sync"]))))


def test_registration_ledger_and_log_roll_back_together(db, monkeypatch):
    from app.services import members

    create_season(db)

    def fail(*args, **kwargs):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(members, "record_event", fail)
    with pytest.raises(RuntimeError), db.begin():
        synchronize_member(db, AuthenticatedUser(uuid4(), "new-google"), operation_id=uuid4())
    assert count(db, Member) == count(db, PointTransaction) == count(db, SystemLog) == 0


def test_cors_allows_frontend_bearer_and_idempotency_headers(client):
    response = client.options(
        "/api/v1/auth/session",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,idempotency-key,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    response = client.options(
        "/api/v1/auth/session", headers={"Origin": "https://unknown.example", "Access-Control-Request-Method": "POST"}
    )
    assert response.status_code == 400


def test_openapi_documents_bearer_and_uuid_header(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/auth/session"]["post"]
    assert operation["security"] == [{"HTTPBearer": []}]
    header = next((row for row in operation["parameters"] if row["name"] == "Idempotency-Key"))
    assert header["required"] and header["schema"]["format"] == "uuid"
    assert "requestBody" not in operation


@pytest.mark.parametrize("status", [301, 429, 500, 503])
def test_auth_upstream_failure_is_sanitized(status):
    with httpx.Client(
        base_url="https://project.supabase.co",
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"error": "private-token-email"})),
    ) as client:
        with pytest.raises(AuthError) as error:
            verify_access_token(TOKEN, client=client)
    assert error.value.status_code == 503 and "private" not in error.value.message


def test_auth_network_timeout_is_sanitized():

    def timeout(request):
        raise httpx.ReadTimeout("private-token-email", request=request)

    with httpx.Client(base_url="https://project.supabase.co", transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(AuthError) as error:
            verify_access_token(TOKEN, client=client)
    assert error.value.status_code == 503 and "private" not in error.value.message


@pytest.mark.parametrize("payload", [[], {}, {"id": "invalid"}, provider_user() | {"identities": None}])
def test_malformed_auth_user_is_not_trusted(payload):
    with pytest.raises(AuthError) as error:
        auth.verified_user(payload)
    assert error.value.status_code == 503


def test_editable_user_metadata_cannot_fake_google_identity():
    payload = provider_user()
    payload["identities"] = [{"provider": "email", "identity_data": {"sub": "fake"}}]
    payload["user_metadata"] = {"provider": "google", "sub": "fake"}
    with pytest.raises(AuthError) as error:
        auth.verified_user(payload)
    assert error.value.code == "google_identity_required"


@pytest.mark.parametrize("mode", ["provider_id", "user_id", "duplicate", "missing_sub", "anonymous", "banned"])
def test_unusable_or_inconsistent_google_identity_is_rejected(mode):
    payload = provider_user()
    if mode == "provider_id":
        payload["identities"][0]["provider_id"] = "different"
    elif mode == "user_id":
        payload["identities"][0]["user_id"] = str(uuid4())
    elif mode == "duplicate":
        payload["identities"] *= 2
    elif mode == "missing_sub":
        del payload["identities"][0]["identity_data"]["sub"]
    elif mode == "anonymous":
        payload["is_anonymous"] = True
    else:
        payload["banned_until"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with pytest.raises(AuthError):
        auth.verified_user(payload)


@pytest.mark.parametrize(
    "url,key",
    [
        (None, None),
        ("http://project.supabase.co", "sb_publishable_test"),
        ("https://project.supabase.co/auth/v1", "sb_publishable_test"),
        ("https://user:pass@project.supabase.co", "sb_publishable_test"),
        ("https://project.supabase.co", "sb_secret_do_not_use"),
        ("postgresql://user:pass@host/db", "sb_publishable_test"),
    ],
)
def test_auth_configuration_requires_https_project_url_and_publishable_key(url, key):
    settings = Settings(_env_file=None, supabase_url=url, supabase_publishable_key=key)
    with pytest.raises(AuthError) as error:
        settings.auth_configuration()
    assert error.value.code == "auth_not_configured"


def test_auth_configuration_never_repr_prints_key():
    settings = Settings(
        _env_file=None,
        supabase_url="https://project.supabase.co/",
        supabase_publishable_key=SecretStr("sb_publishable_private-test-value"),
    )
    assert settings.auth_configuration() == ("https://project.supabase.co", "sb_publishable_private-test-value")
    assert "private-test-value" not in repr(settings)


def test_missing_auth_config_returns_503_without_disabling_public_catalog(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "")
    get_settings.cache_clear()
    assert client.get("/api/v1/health").status_code == 200
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 503 and response.json()["detail"]["code"] == "auth_not_configured"


def test_http_expired_session_commits_status_and_reregistration_cannot_extend(client, db, authenticated, monkeypatch):
    from app.services import sessions

    headers = authenticated["headers"]
    assert client.post("/api/v1/auth/session", headers=headers).status_code == 200
    row = db.scalar(select(MemberSession))
    expiry = sessions.utc(row.expires_at)
    created = sessions.utc(row.create_dt)
    db.commit()
    monkeypatch.setattr(sessions, "now_utc", lambda: expiry)
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    db.refresh(row)
    assert row.status == "expired"
    db.commit()
    headers = {**headers, "Idempotency-Key": str(uuid4())}
    assert client.post("/api/v1/auth/session", headers=headers).status_code == 401
    db.refresh(row)
    assert sessions.utc(row.create_dt) == created and sessions.utc(row.expires_at) == expiry


def test_http_logout_blocks_reuse_but_keeps_member_balance(client, db, authenticated):

    headers = authenticated["headers"]
    create_season(db)
    client.post("/api/v1/auth/session", headers=headers)
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/session", headers=headers).status_code == 401
    assert db.scalar(select(MemberSession.status)) == "revoked"
    assert db.scalar(select(Member.current_points)) == 100


def test_upstream_logout_revokes_server_session(client, db, authenticated, monkeypatch):
    from app.services import sessions

    headers = authenticated["headers"]
    client.post("/api/v1/auth/session", headers=headers)

    def gone(*args):
        raise AuthError(401, "upstream_session_missing", "Login again.")

    monkeypatch.setattr(sessions, "native_session_created", gone)
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert db.scalar(select(MemberSession.status)) == "revoked"


def test_bearer_without_registered_server_session_cannot_access_me(client, db, authenticated):
    user = auth.verified_user(authenticated["payload"])
    db.add(Member(auth_user_id=user.user_id, google_sub=user.google_sub))
    db.commit()
    assert client.get("/api/v1/auth/me", headers=authenticated["headers"]).json()["detail"]["code"] == "session_missing"


@pytest.mark.parametrize("method", ["password", "otp", "magiclink", "anonymous", "sso/saml"])
def test_google_linked_account_cannot_use_other_authentication_method(method):
    now = datetime.now(timezone.utc)
    payload = provider_user()
    claims = dict(
        sub=payload["id"],
        iss="https://project.supabase.co/auth/v1",
        aud="authenticated",
        role="authenticated",
        exp=int((now + timedelta(hours=1)).timestamp()),
        session_id=str(uuid4()),
        amr=[{"method": method, "timestamp": int(now.timestamp())}],
    )
    token = "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"
    with httpx.Client(
        base_url="https://project.supabase.co",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    ) as client:
        with pytest.raises(AuthError, match="google_login_required"):
            verify_access_token(token, client=client)


def test_google_and_other_provider_linked_account_is_rejected():
    payload = provider_user()
    payload["identities"].append({"provider": "email"})
    with pytest.raises(AuthError, match="google_only_required"):
        auth.verified_user(payload)
