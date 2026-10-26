"""Caller owns the transaction. Lock order: seasons, then member.

Creation is idempotent through existing UUID/Google-sub unique constraints.
These functions never trust identity data from request bodies.
"""

from uuid import UUID
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.auth import AuthenticatedUser, AuthError
from app.models import Member, Season, SystemLog
from app.db.compact import key_hash
from app.services.activity import record_event
from app.services.member_locks import lock_member_rows
from app.services.points import open_member_season, verify_member_ledger


def matching_member(db: Session, user: AuthenticatedUser, *, lock: bool = False) -> Member | None:
    query = (
        select(Member)
        .where(or_(Member.auth_user_id == user.user_id, Member.google_sub == user.google_sub))
        .order_by(Member.id)
    )
    members = list(db.scalars(query))
    if lock:
        members = lock_member_rows(db, [member.id for member in members])
    if not members:
        return None
    if len(members) != 1 or members[0].auth_user_id != user.user_id or members[0].google_sub != user.google_sub:
        raise AuthError(409, "member_identity_conflict", "The existing member identity requires administrator review.")
    return members[0]


def synchronize_member(
    db: Session, user: AuthenticatedUser, *, operation_id: UUID, request_id: UUID | None = None
) -> Member:
    from app.services.season_locks import registration_seasons
    active = registration_seasons(db)
    dialect = db.get_bind().dialect.name
    insert = postgres_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
    if insert is None:
        raise RuntimeError("Unsupported member registration database.")
    statement = insert(Member.__table__).values(auth_user_id=user.user_id, google_sub=user.google_sub)
    inserted = db.execute(statement.on_conflict_do_nothing().returning(Member.id)).scalar_one_or_none() is not None
    member = matching_member(db, user, lock=True)
    if member is None:
        raise RuntimeError("Member registration did not produce a member.")
    from app.models import PointTransaction
    for season in active:
        if db.scalar(select(PointTransaction.id).where(PointTransaction.member_id == member.id,
                PointTransaction.season_id == season.id, PointTransaction.reason == "season_open")) is None:
            open_member_season(db, member, season)
        verify_member_ledger(db, member, season.id)
    verify_member_ledger(db, member)
    audit_season = active[0].id if len(active) == 1 else None
    if inserted:
        record_event(
            db,
            "auth.member.created",
            actor_member_id=member.id,
            season_id=audit_season,
            request_id=request_id,
            event_key=f"member:{member.id}:created",
        )
    key = f"auth:sync:{member.id}:{operation_id}"
    previous = db.scalar(select(SystemLog).where(SystemLog.event_key_hash == key_hash(key)))
    if previous is None:
        record_event(
            db,
            "auth.session.sync",
            actor_member_id=member.id,
            season_id=audit_season,
            request_id=request_id,
            event_key=key,
        )
    elif previous.event_name != "auth.session.sync" or previous.actor_member_id != member.id:
        raise AuthError(409, "operation_key_conflict", "The operation key was already used for another operation.")
    db.flush()
    return member
