from app.models import Member
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from app.core.auth import AuthenticatedUser, AuthError
from app.services import sessions


@pytest.fixture
def user(db, monkeypatch):
    now = datetime.now(timezone.utc)
    user = AuthenticatedUser(uuid4(), "google", uuid4(), now)
    db.add(Member(auth_user_id=user.user_id, google_sub=user.google_sub))
    db.commit()
    monkeypatch.setattr(sessions, "native_session_created", lambda db, user: now)
    return user


def test_refresh_and_new_operation_never_extend_clock(db, user, monkeypatch):
    first = datetime.now(timezone.utc)
    monkeypatch.setattr(sessions, "now_utc", lambda: first)
    row = sessions.register_session(db, user)
    db.commit()
    old_expiry = sessions.utc(row.expires_at)
    monkeypatch.setattr(sessions, "now_utc", lambda: first + timedelta(hours=5))
    repeat = sessions.register_session(db, user)
    assert repeat.id == row.id
    assert sessions.utc(repeat.create_dt) == first
    assert sessions.utc(repeat.expires_at) == old_expiry == first + timedelta(hours=6)


def test_six_hour_boundary_expires_and_cannot_reactivate(db, user, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(sessions, "now_utc", lambda: now)
    row = sessions.register_session(db, user)
    assert sessions.session_error(row, now + timedelta(hours=6, microseconds=-1)) is None
    error = sessions.session_error(row, now + timedelta(hours=6))
    assert error.code == "session_expired" and row.status == "expired"
    assert row.revoked_at is None
    with pytest.raises(AuthError, match="session_expired"):
        sessions.register_session(db, replace(user, oauth_at=now + timedelta(hours=6)))


def test_revoked_session_never_reactivates_and_new_google_session_can_register(db, user):
    row = sessions.register_session(db, user)
    sessions.revoke_session(row)
    assert row.status == "revoked" and row.revoked_at is not None
    with pytest.raises(AuthError, match="session_revoked"):
        sessions.register_session(db, user)
    new = replace(user, session_id=uuid4(), oauth_at=datetime.now(timezone.utc))
    newer = sessions.register_session(db, new)
    assert newer.status == "active" and newer.id != row.id


@pytest.mark.parametrize("native_age,oauth_age", [(360, 0), (0, 360), (-60, 0)])
def test_old_or_future_native_session_cannot_mint_six_hours(db, user, native_age, oauth_age):
    now = datetime.now(timezone.utc)
    with pytest.raises(AuthError, match="fresh_google_login_required"):
        sessions.register_session(
            db,
            replace(user, oauth_at=now - timedelta(seconds=oauth_age)),
            native_created=now - timedelta(seconds=native_age),
        )


def test_session_uuid_cannot_be_used_by_another_member(db, user):
    sessions.register_session(db, user)
    with pytest.raises(AuthError, match="session_identity_mismatch"):
        sessions.session_for_user(db, replace(user, user_id=uuid4()))


def test_expiry_sweep_only_changes_active_elapsed_sessions(db, user):
    row = sessions.register_session(db, user)
    row.create_dt = datetime.now(timezone.utc) - timedelta(hours=7)
    row.expires_at = row.create_dt + timedelta(hours=6)
    db.flush()
    assert sessions.expire_sessions(db) == 1
    db.refresh(row)
    assert row.status == "expired"
    assert sessions.expire_sessions(db) == 0


def test_status_is_not_accepted_without_real_clock_check(db, user):
    row = sessions.register_session(db, user)
    row.status = "active"
    assert sessions.session_error(row, sessions.utc(row.expires_at)).code == "session_expired"
