"""Internal dashboard queries; never exposed without administrator authorization."""

from datetime import datetime
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
from app.models import SystemLog, Season
from app.services.activity import EVENTS
from app.db.schema import CODES
from app.db.compact import code_for


def feature_usage(
    db: Session, start: datetime, end: datetime, *, season_id: str | None = None, source: str | None = None
) -> list[dict]:
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("Use an ordered timezone-aware [start, end) interval.")
    if source is not None and source not in {"api", "business", "system", "admin"}:
        raise ValueError("Invalid event source.")
    conditions = [
        SystemLog.created_at >= start,
        SystemLog.created_at < end,
        SystemLog.event_name.not_in(["http.request.started", "worker.sync.started"]),
    ]
    if season_id is not None:
        conditions.append(
            SystemLog.season_id == select(Season.id).where(Season.public_code == season_id).scalar_subquery()
        )
    if source is not None:
        conditions.append(SystemLog.source == source)
    route_features = {
        code: (
            "teams"
            if code == 3
            else "seasons"
            if code in (4, 5)
            else "matches"
            if code in (6, 7, 14)
            else "auth"
            if code in (8, 9, 10)
            else "http"
        )
        for code in CODES["route"]
    }
    business_features = {code_for("event", name): parts[0] for name, parts in EVENTS.items()}
    business_actions = {code_for("event", name): parts[1] for name, parts in EVENTS.items()}
    feature = case(
        (SystemLog.event_code.in_([1, 2, 3]), case(route_features, value=SystemLog.route_code, else_="http")),
        (SystemLog.event_code.in_([36, 37]), "matches"),
        else_=case(business_features, value=SystemLog.event_code, else_="legacy"),
    )
    action = case(
        (
            SystemLog.event_code.in_([1, 2, 3]),
            func.lower(case(CODES["http_method"], value=SystemLog.http_method, else_="OTHER")),
        ),
        (SystemLog.event_code.in_([36, 37]), "sync"),
        else_=case(business_actions, value=SystemLog.event_code, else_="unknown"),
    )
    statement = (
        select(
            SystemLog.source.label("source"),
            SystemLog.event_name.label("event_name"),
            feature.label("feature"),
            action.label("action"),
            func.count().label("total"),
            func.sum(case((SystemLog.outcome == "success", 1), else_=0)).label("success"),
            func.sum(case((SystemLog.outcome == "failure", 1), else_=0)).label("failure"),
            func.sum(case((SystemLog.outcome == "rejected", 1), else_=0)).label("rejected"),
            func.count(func.distinct(SystemLog.actor_member_id)).label("unique_members"),
            func.avg(SystemLog.duration_ms).label("average_duration_ms"),
        )
        .where(*conditions)
        .group_by(SystemLog.source, SystemLog.event_name, feature, action)
        .order_by(SystemLog.source, feature, action)
    )
    return [dict(row) for row in db.execute(statement).mappings()]
