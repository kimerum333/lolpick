from compact_fixtures import internal
from app.models import Season, Member
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from app.core.config import get_settings
from app.models import SystemLog
from app.services.activity import record_event
from app.services.analytics import feature_usage


def test_business_event_key_deduplicates_and_rejects_mismatched_event(db):
    first = record_event(db, "auth.login", event_key="login-operation-1")
    second = record_event(db, "auth.login", event_key="login-operation-1")
    assert first.id == second.id
    assert db.scalar(select(func.count()).select_from(SystemLog)) == 1
    with pytest.raises(ValueError, match="different event"):
        record_event(db, "bet.placed", event_key="login-operation-1")


@pytest.mark.parametrize(
    "context",
    [
        {"access_token": "secret"},
        {"email": "private@example.com"},
        {"ip": "127.0.0.1"},
        {"request_body": {"password": "secret"}},
    ],
)
def test_log_context_rejects_sensitive_and_unregistered_fields(db, context):
    with pytest.raises(ValueError, match="allowlist"):
        record_event(db, "auth.login", context=context)
    assert db.scalar(select(func.count()).select_from(SystemLog)) == 0


def test_dashboard_filters_season_source_time_and_counts_success_failure(db):
    start = datetime(2026, 10, 6, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    db.add(Season(public_code="s1", slug="worlds", year=2026, name="Worlds", label="Worlds", status="active"))
    db.flush()
    member_id = uuid4()
    db.add(Member(auth_user_id=member_id, google_sub="analytics-test"))
    db.flush()
    for outcome, when, actor in [
        ("success", start, member_id),
        ("failure", start + timedelta(hours=1), member_id),
        ("rejected", start + timedelta(hours=2), None),
        ("success", end, None),
    ]:
        record_event(
            db,
            "catalog.matches.view",
            season_id=internal(db, Season, "s1"),
            outcome=outcome,
            actor_member_id=internal(db, Member, actor),
            duration_ms=10,
            occurred_at=when,
        )
    record_event(
        db,
        "bet.placed",
        season_id=internal(db, Season, "s1"),
        actor_member_id=internal(db, Member, member_id),
        occurred_at=start,
    )
    rows = feature_usage(db, start, end, season_id="s1", source="api")
    assert len(rows) == 1
    assert rows[0]["total"] == 3
    assert rows[0]["success"] == rows[0]["failure"] == rows[0]["rejected"] == 1
    assert rows[0]["unique_members"] == 1
    assert rows[0]["average_duration_ms"] == 10
    with pytest.raises(ValueError, match="timezone-aware"):
        feature_usage(db, start.replace(tzinfo=None), end)


def test_http_logging_includes_health_and_uses_server_request_id(client, db, monkeypatch):
    from app import telemetry

    recorded = []
    db.add(Season(public_code="s1", slug="audit", year=2026, name="Audit", label="Audit", status="active"))
    db.commit()
    monkeypatch.setenv("SYSTEM_LOG_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(
        telemetry,
        "mirror_event",
        lambda event: recorded.append((event.copy(),)) if event.get("event_name") == "http.request" else None,
    )
    response = client.get("/api/v1/health", headers={"X-Request-ID": "untrusted"})
    assert response.status_code == 200 and len(recorded) == 1
    response = client.get("/api/v1/matches", params={"season_id": "s1"}, headers={"X-Request-ID": "untrusted"})
    assert len(recorded) == 2 and recorded[1][0]["feature"] == "matches"
    assert recorded[1][0]["status_code"] == 200 and recorded[1][0]["season_id"] == internal(db, Season, "s1")
    assert response.headers["X-Request-ID"] == recorded[1][0]["request_id"]
    assert response.headers["X-Request-ID"] != "untrusted"


def test_metric_storage_failure_is_contained(monkeypatch):
    from app import telemetry

    def unavailable():
        raise RuntimeError("private database details")

    monkeypatch.setattr(telemetry, "get_audit_engine", unavailable)
    telemetry.persist_http_event({"request_id": str(uuid4())})
