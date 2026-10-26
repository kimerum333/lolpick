"""Server operator only: grant/remove an administrator after member registration."""

import argparse
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy import create_engine
from app.core.config import get_settings, DatabaseConfigurationError
from app.db.session import connection_options
from app.models import ADMIN_TYPE, MEMBER_TYPE, Member
from app.services.activity import record_event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("member_id", type=UUID)
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if settings.migration_database_url is None:
        raise DatabaseConfigurationError(
            "Administrator provisioning requires an explicit MIGRATION_DATABASE_URL operator credential."
        )
    engine = create_engine(
        settings.connection_url(migration=True), hide_parameters=True, connect_args=connection_options(settings)
    )
    try:
        with Session(engine) as db, db.begin():
            member = db.scalar(select(Member).where(Member.auth_user_id == args.member_id).with_for_update())
            if member is None:
                raise ValueError("Member must register with Google first.")
            member.type = MEMBER_TYPE if args.revoke else ADMIN_TYPE
            record_event(
                db,
                "admin.role.changed",
                actor_member_id=member.id,
                context={"reason": "revoke" if args.revoke else "grant"},
            )
        print("Administrator permission updated.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
