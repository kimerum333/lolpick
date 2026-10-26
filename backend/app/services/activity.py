"""Structured server events. No request bodies, tokens, emails, IPs or raw URLs."""

from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import SystemLog
from app.db.compact import key_hash, code_for, checked_document

EVENTS = {
    "catalog.teams.view": ("teams", "view", "api"),
    "catalog.seasons.view": ("seasons", "view", "api"),
    "catalog.matches.view": ("matches", "view", "api"),
    "points.changed": ("points", "change", "business"),
    "admin.role.changed": ("admin", "role_change", "admin"),
    "season.finished": ("seasons", "finish", "admin"),
    "season.activated": ("seasons", "activate", "system"),
    "auth.member.created": ("auth", "register", "business"),
    "auth.session.sync": ("auth", "sync", "api"),
    # Reserved until the corresponding authenticated services are implemented.
    "auth.login": ("auth", "login", "business"),
    "bet.placed": ("bets", "place", "business"),
    "bet.settled": ("bets", "settle", "system"),
    "bet.refunded": ("bets", "refund", "system"),
    "cheer.contracted": ("cheers", "contract", "business"),
    "settlement.blocked": ("matches", "settle", "system"),
    "matches.synced": ("matches", "sync", "system"),
}
CONTEXT_KEYS = {
    "status_code",
    "match_id",
    "count",
    "error_type",
    "inserted",
    "updated",
    "skipped",
    "conflicts",
    "series_id",
    "pool",
    "paid",
    "removed_remainder",
    "error_code",
    "settlement_blocked",
    "reason",
    "entry_id",
}


def record_event(
    db: Session,
    event_name: str,
    *,
    outcome: str = "success",
    season_id: int | None = None,
    actor_member_id: int | None = None,
    request_id: UUID | None = None,
    event_key: str | None = None,
    duration_ms: int | None = None,
    context: dict | None = None,
    occurred_at: datetime | None = None,
) -> SystemLog:
    if event_name not in EVENTS:
        raise ValueError("Unregistered system event.")
    if outcome not in {"success", "failure", "rejected"}:
        raise ValueError("Invalid event outcome.")
    if duration_ms is not None and (type(duration_ms) is not int or duration_ms < 0):
        raise ValueError("duration_ms must be a nonnegative integer.")
    if event_key is not None and (not event_key or len(event_key) > 200):
        raise ValueError("Invalid event key.")
    context = dict(context or {})
    if set(context) - CONTEXT_KEYS:
        raise ValueError("Log context contains fields outside the allowlist.")
    if any(
        type(value) not in (str, int, bool) or (isinstance(value, str) and len(value) > 160)
        for value in context.values()
    ):
        raise ValueError("Log context must contain small scalar values.")
    occurred_at = occurred_at or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        raise ValueError("Log time must include a timezone.")
    if event_key:
        existing = db.scalar(select(SystemLog).where(SystemLog.event_key_hash == key_hash(event_key)))
        if existing:
            if (existing.event_name, existing.season_id, existing.actor_member_id, existing.outcome) != (
                event_name,
                season_id,
                actor_member_id,
                outcome,
            ):
                raise ValueError("Event key was reused for a different event.")
            return existing
    _, _, source = EVENTS[event_name]
    status_code = context.pop("status_code", None)
    context = checked_document(context, CONTEXT_KEYS - {"status_code"}, 1024)
    row = SystemLog(
        event_code=code_for("event", event_name),
        source=source,
        outcome=outcome,
        severity={"success": "info", "rejected": "warning", "failure": "error"}[outcome],
        season_id=season_id,
        actor_member_id=actor_member_id,
        request_id=request_id,
        event_key_hash=key_hash(event_key) if event_key else None,
        duration_ms=duration_ms,
        status_code=status_code,
        context=context,
        created_at=occurred_at,
    )
    db.add(row)
    db.flush()
    return row
