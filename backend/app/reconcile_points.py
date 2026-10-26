"""Read-only, consistent ledger reconciliation. Never auto-repair member balances."""
import json
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.db.session import get_engine, dispose_engine
from app.models import Member, PointTransaction
from app.services.points import verify_member_ledger, PointOperationError


def reconcile(db):
    checked, issues = 0, []
    for member in db.scalars(select(Member).order_by(Member.id).execution_options(yield_per=1000)):
        checked += 1
        try:
            verify_member_ledger(db, member)
            for season_id in db.scalars(select(PointTransaction.season_id).where(
                    PointTransaction.member_id == member.id).distinct()):
                verify_member_ledger(db, member, season_id)
        except PointOperationError as exc:
            issues.append({"member_id": str(member.id), "season_id": member.current_season_id, "code": exc.code})
    return {"checked": checked, "consistent": not issues, "issues": issues}


def main():
    try:
        with Session(get_engine()) as db, db.begin():
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            result = reconcile(db)
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(0 if result["consistent"] else 1)
    finally:
        dispose_engine()


if __name__ == "__main__":
    main()
