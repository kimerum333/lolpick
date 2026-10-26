"""Operator-only read-only match point ranking; no public member leaderboard."""

import argparse
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import dispose_engine, get_engine
from app.db.schema import REVISION
from app.services.point_reports import PointReportError, match_stake_ranking


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season-id", required=True)
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--order-by", choices=["amount", "time"], default="amount")
    parser.add_argument("--include-refunded", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    try:
        with Session(get_engine()) as db, db.begin():
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            if db.scalar(text("SELECT version_num FROM lolpick.lolpick_alembic_version")) != REVISION:
                raise PointReportError("Apply the expected database migrations before running this report.")
            result = match_stake_ranking(
                db,
                season_id=args.season_id,
                match_id=args.match_id,
                order_by=args.order_by,
                include_refunded=args.include_refunded,
                limit=args.limit,
                offset=args.offset,
            )
        print(json.dumps(result, ensure_ascii=True))
    except PointReportError as exc:
        print(json.dumps({"error": {"code": "invalid_report", "message": str(exc)}}))
        raise SystemExit(2) from None
    except Exception as exc:
        print(json.dumps({"error": {"code": "report_unavailable", "errorType": type(exc).__name__}}))
        raise SystemExit(1) from None
    finally:
        dispose_engine()


if __name__ == "__main__":
    main()
