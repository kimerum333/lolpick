"""Registration/activation gate; ordinary season work locks only its target.

The gate protects the member snapshot taken by activation. Registrations share
it; activation takes it exclusively before any season/session/member lock.
It does not gate betting, settlement, finish, or provider collection.
"""
from sqlalchemy import select, text
from app.models import Season


def membership_gate(db, *, exclusive=False):
    if db.get_bind().dialect.name == "postgresql":
        function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
        db.execute(text(f"SELECT {function}(90520341)"))


def registration_seasons(db):
    membership_gate(db)
    return list(db.scalars(select(Season).where(
        Season.status == "active", Season.is_demo.is_(False)
    ).order_by(Season.id).with_for_update(read=True).execution_options(populate_existing=True)))


def registration_identity(db, user):
    # Serialize absent session rows too. A hash collision only delays another
    # registration; identity matching still uses the full verified UUID/sub.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(90520342, hashtext(:identity))"),
                   {"identity": str(user.user_id)})
