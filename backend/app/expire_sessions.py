"""Optional operator task: synchronize expired statuses for idle sessions."""
from sqlalchemy.orm import Session
from app.db.session import get_engine
from app.services.sessions import expire_sessions


def main():
    engine = get_engine()
    try:
        with Session(engine) as db, db.begin():
            count = expire_sessions(db)
        print(f"Expired {count} sessions.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
