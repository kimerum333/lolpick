"""Lock members without granting the runtime direct balance/admin writes.

Caller must hold season, then match locks first. PostgreSQL locks remain held
until the outer transaction ends. Refresh identity-map values after acquiring.
"""
from sqlalchemy import select, text
from app.models import Member


def lock_member_rows(db, identities):
    identities = sorted(set(identities))
    if not identities:
        return []
    if any(type(value) is not int or value <= 0 for value in identities):
        raise ValueError("Member lock identities must be positive integers.")
    query = select(Member).where(Member.id.in_(identities)).order_by(Member.id)
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT id FROM lolpick.lock_members(CAST(:identities AS bigint[]))"),
                   {"identities": identities}).all()
    else:
        query = query.with_for_update()
    return list(db.scalars(query.execution_options(populate_existing=True)))
