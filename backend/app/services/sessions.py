"""Server-owned six-hour sessions. Caller commits state transitions.

A refresh keeps the native session UUID and cannot reset create_dt. The upstream
session must still exist; its Google-only identity/AMR were verified separately.
"""

from datetime import datetime, timedelta, timezone
from sqlalchemy import insert, select, text, update
from app.core.auth import AuthError
from sqlalchemy.orm import object_session
from app.models import MemberSession, Member

LIFETIME = timedelta(hours=6)
REGISTRATION_WINDOW = timedelta(minutes=5)


def now_utc():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def native_session_created(db, user):
    # Supabase auth.sessions uses RLS. The private definer lookup returns only a
    # matching user's session metadata; runtime gets no direct auth table grant.
    row = db.execute(
        text("SELECT user_id,created_at FROM lolpick.read_native_session(:id,:user_id)"),
        {"id": user.session_id, "user_id": user.user_id}
    ).first()
    if row is None or str(row.user_id) != str(user.user_id):
        raise AuthError(401, "upstream_session_missing", "Perform a new Google login.")
    if not isinstance(row.created_at, datetime):
        raise AuthError(503, "upstream_session_invalid", "Authentication session metadata is unavailable.")
    return utc(row.created_at)


def session_for_user(db, user):
    if user.session_id is None:
        raise AuthError(401, "google_login_required", "Perform a new Google login.")
    row = db.scalar(select(MemberSession).where(MemberSession.auth_session_id == user.session_id).with_for_update().execution_options(populate_existing=True))
    if row is not None and db.get(Member, row.member_id).auth_user_id != user.user_id:
        raise AuthError(401, "session_identity_mismatch", "Invalid session identity.")
    return row


def session_error(row, now):
    if row.status == "active" and now >= utc(row.expires_at):
        row.status = "expired"
        db = object_session(row)
        if db is None or db.get_bind().dialect.name != "postgresql":
            row.status_changed_at = now
    if row.status != "active":
        return AuthError(401, "session_" + row.status, "Perform a new Google login.")
    return None


def register_session(db, user, *, row=None, native_created=None):
    now = now_utc()
    row = row if row is not None else session_for_user(db, user)
    if row is not None:
        error = session_error(row, now)
        if error:
            raise error
        return row  # Same UUID, including a new Idempotency-Key: clock unchanged.
    native_created = native_created or native_session_created(db, user)
    if (
        user.oauth_at is None
        or not timedelta(0) <= now - utc(native_created) <= REGISTRATION_WINDOW
        or not timedelta(0) <= now - utc(user.oauth_at) <= REGISTRATION_WINDOW
    ):
        raise AuthError(401, "fresh_google_login_required", "Register within five minutes of a new Google login.")
    member_id = db.scalar(select(Member.id).where(Member.auth_user_id == user.user_id))
    if member_id is None:
        raise AuthError(401, "member_not_registered", "Register the member first.")
    if db.get_bind().dialect.name == "postgresql":
        identity = db.scalar(insert(MemberSession.__table__).values(
            auth_session_id=user.session_id, member_id=member_id
        ).returning(MemberSession.id))
        return db.get(MemberSession, identity)
    row = MemberSession(
        auth_session_id=user.session_id,
        member_id=member_id,
        create_dt=now,
        expires_at=now + LIFETIME,
        status="active",
        status_changed_at=now,
    )
    db.add(row)
    db.flush()
    return row


def expire_sessions(db):
    now = now_utc()
    values = {"status": "expired"}
    if db.get_bind().dialect.name != "postgresql":
        values["status_changed_at"] = now
    return db.execute(
        update(MemberSession)
        .where(MemberSession.status == "active", MemberSession.expires_at <= now)
        .values(**values)
    ).rowcount


def revoke_session(row):
    if row.status == "active":
        row.status = "revoked"
        db = object_session(row)
        if db is None or db.get_bind().dialect.name != "postgresql":
            row.revoked_at = row.status_changed_at = now_utc()
