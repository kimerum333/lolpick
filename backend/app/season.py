"""Explicit operator command: atomic season activation and balance reset."""
import argparse
from sqlalchemy.orm import Session
from app.db.session import get_engine
from app.services.points import activate_season, finish_season


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("season_id")
    parser.add_argument("--finish", action="store_true", help="Finish explicitly without activating another season.")
    args = parser.parse_args()
    engine = get_engine()
    try:
        with Session(engine) as db, db.begin():
            if args.finish:
                finish_season(db, args.season_id)
                count = 0
            else:
                count = activate_season(db, args.season_id)
        print(f"Season command complete; {count} member balances initialized.")
    except Exception as exc:
        print(f"Season activation failed ({type(exc).__name__}); transaction rolled back. Check unfinished settlement and target status.")
        raise SystemExit(1) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
