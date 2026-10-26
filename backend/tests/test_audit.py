import json
from datetime import datetime
from fastapi import HTTPException
from app.core.config import get_settings
from app.telemetry import captured_json, redact, LIMIT


def capture(monkeypatch):
    from app import telemetry

    events = []
    monkeypatch.setenv("SYSTEM_LOG_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(
        telemetry,
        "mirror_event",
        lambda event: events.append(event.copy()) if event.get("event_name") == "http.request" else None,
    )
    return events


def test_every_api_including_validation_404_health_and_options_is_logged(client, monkeypatch):
    events = capture(monkeypatch)
    client.get("/api/v1/health")
    client.get("/api/v1/matches", params={"limit": "wrong", "access_token": "SECRET"})
    client.get("/missing")
    client.options(
        "/api/v1/matches", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"}
    )
    assert [e["status_code"] for e in events] == [200, 422, 404, 200]
    assert "message" not in events[0] and "message" not in events[3]
    assert "SECRET" not in json.dumps(events)
    assert datetime.fromisoformat(events[0]["created_at"]).tzinfo is not None
    assert events[1]["message"]["input"]["query_bytes"] > 0
    assert "wrong" not in json.dumps(events)


def test_error_body_records_shape_without_untrusted_values(client, monkeypatch):
    events = capture(monkeypatch)

    @client.app.post("/audit-test")
    async def fail(request: __import__("fastapi").Request):
        await request.json()
        raise HTTPException(422, detail="invalid amount")

    response = client.post("/audit-test", json={"amount": -10, "nested": {"refreshToken": "SECRET", "team_id": "A"}})
    assert response.status_code == 422
    message = events[-1]["message"]
    assert message["input"]["body"]["field_count"] == 2
    assert message["input"]["body"]["unknown_field_count"] == 2
    assert "team_id" not in json.dumps(message)
    assert "SECRET" not in json.dumps(message)


def test_db_failure_keeps_text_log_and_response(client, monkeypatch, tmp_path):
    from app import telemetry

    written = []

    class Logger:
        def info(self, value):
            written.append(value)

        def error(self, value):
            written.append(value)

    monkeypatch.setattr(telemetry, "audit_logger", lambda: Logger())
    monkeypatch.setenv("SYSTEM_LOG_ENABLED", "true")
    get_settings.cache_clear()

    def fail():
        raise RuntimeError("PRIVATE_DATABASE_PASSWORD")

    monkeypatch.setattr(telemetry, "get_audit_engine", fail)
    assert client.get("/api/v1/health").status_code == 200
    from app.audit_journal import get_journal

    assert get_journal().replay(telemetry.persist_http_event) == 0
    assert get_journal().summary()["pending"] == 2
    assert "PRIVATE_DATABASE_PASSWORD" not in "".join(written)
    assert json.loads(written[-1])["event_name"] == "audit.db_unavailable"


def test_malformed_or_large_payload_is_explicitly_omitted():
    assert captured_json(b'{"password":"SECRET"', True)["capture"] == "invalid_or_non_json_omitted"
    assert captured_json(b"x" * LIMIT, False)["capture"] == "incomplete_or_over_limit"
    assert "SECRET" not in str(redact({"loc": ["body", "password"], "input": "SECRET"}))


def test_unhandled_exception_is_safe_and_audited(client, monkeypatch):
    events = capture(monkeypatch)

    @client.app.get("/crash-test")
    def crash():
        raise RuntimeError("PRIVATE_TOKEN_SECRET")

    result = client.get("/crash-test")
    assert result.status_code == 500
    assert "PRIVATE_TOKEN_SECRET" not in result.text + json.dumps(events)
    assert events[-1]["message"]["error_type"] == "RuntimeError"


def test_file_rotation_keeps_private_permissions(tmp_path):
    import os
    import stat
    import logging
    import pytest
    from app.telemetry import PrivateRotatingHandler

    if os.name == "nt":
        pytest.skip("Windows file privacy is controlled by NTFS ACL, not POSIX mode bits.")
    path = tmp_path / "private.log"
    handler = PrivateRotatingHandler(path, maxBytes=30, backupCount=1)
    logger = logging.Logger("rotation-test")
    logger.addHandler(handler)
    logger.warning("first record long enough")
    logger.warning("second record causes rotation")
    handler.close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "private.log.1").stat().st_mode) == 0o600
