"""Failure injection: keep public pages usable without relaxing write/auth checks."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError
from app.api.public_availability import PublicAvailabilityMiddleware, PublicSnapshots, MAX_ENTRIES
from app.audit_journal import JournalUnavailable
from app.core.config import get_settings
from demo_seed import seed_demo
from app.services import catalog


def outage(*args, **kwargs):
    raise OperationalError("PRIVATE_SQL", {}, RuntimeError("PRIVATE_PASSWORD"))


def warm(client, db, path="/api/v1/matches"):
    seed_demo(db)
    db.commit()
    response = client.get(path)
    assert response.status_code == 200
    return response


def test_db_failure_uses_last_good_public_page_and_closes_betting(client, db, monkeypatch):
    from test_pandascore import apply, match_payload
    from compact_fixtures import lookup
    from app.models import Season
    apply(db, match_payload())
    lookup(db, Season, "test-2026").status = "active"
    db.commit()
    assert client.get("/api/v1/matches").json()["items"][0]["bettingOpen"] is True
    monkeypatch.setattr(catalog, "list_matches", outage)
    response = client.get("/api/v1/matches", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 200
    assert response.json()["items"][0]["bettingOpen"] is False
    assert "cached_snapshot" in response.json()["items"][0]["dataIssues"]
    assert response.headers["X-Data-Stale"] == "true"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert "PRIVATE" not in response.text


def test_cache_miss_does_not_fabricate_an_empty_success(client, monkeypatch):
    monkeypatch.setattr(catalog, "list_matches", outage)
    response = client.get("/api/v1/matches")
    assert response.status_code == 503 and "items" not in response.json()
    assert response.headers["Retry-After"] == "5"
    assert "database connectivity" not in response.text and "PRIVATE" not in response.text


def test_snapshot_is_bound_to_the_exact_query(client, db, monkeypatch):
    warm(client, db)
    monkeypatch.setattr(catalog, "list_matches", outage)
    assert client.get("/api/v1/matches?limit=1").status_code == 503
    assert client.get("/api/v1/matches").status_code == 200


@pytest.mark.parametrize("headers", [{"Authorization": "Basic PRIVATE"}, {"Cookie": "private=1"}])
def test_fallback_does_not_bypass_supplied_credentials_or_cookie_requests(client, db, monkeypatch, headers):
    warm(client, db)
    monkeypatch.setattr(catalog, "list_matches", outage)
    assert client.get("/api/v1/matches").status_code == 200  # Open the short circuit.
    response = client.get("/api/v1/matches", headers=headers)
    assert response.status_code == (401 if "Authorization" in headers else 503)
    assert "X-Data-Stale" not in response.headers


def test_snapshot_is_not_used_for_invalid_get_bodies(client, db, monkeypatch):
    warm(client, db)
    monkeypatch.setattr(catalog, "list_matches", outage)
    client.get("/api/v1/matches")
    response = client.request("GET", "/api/v1/matches", json={"private": True})
    assert response.status_code == 422 and "X-Data-Stale" not in response.headers


def test_expired_snapshot_is_not_served(client, db, monkeypatch):
    warm(client, db)
    store = client.app.state.public_snapshots
    created = next(iter(store.entries.values())).created
    store.clock = lambda: created + 86401
    monkeypatch.setattr(catalog, "list_matches", outage)
    assert client.get("/api/v1/matches").status_code == 503


def test_circuit_skips_repeated_failing_db_calls_then_probes_recovery(client, db, monkeypatch):
    warm(client, db)
    store = client.app.state.public_snapshots
    now = [store.clock()]
    store.clock = lambda: now[0]
    calls = []
    original = catalog.list_matches
    def unavailable(*args, **kwargs):
        calls.append(1)
        return outage()
    monkeypatch.setattr(catalog, "list_matches", unavailable)
    assert client.get("/api/v1/matches").status_code == 200
    assert client.get("/api/v1/matches").status_code == 200
    assert len(calls) == 1
    now[0] += 6
    monkeypatch.setattr(catalog, "list_matches", original)
    response = client.get("/api/v1/matches")
    assert response.status_code == 200 and "X-Data-Stale" not in response.headers


def test_audit_failure_preserves_public_snapshot_but_blocks_writes(client, db, monkeypatch):
    from app import telemetry
    warm(client, db)
    reached = []
    @client.app.post("/would-mutate")
    def write():
        reached.append(True)
        return {"done": True}
    def unavailable(*args):
        raise JournalUnavailable("PRIVATE_STORAGE")
    monkeypatch.setattr(telemetry, "emit_event", unavailable)
    assert client.get("/api/v1/matches").status_code == 200
    response = client.post("/would-mutate", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 503 and reached == []
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert "audit journal" not in response.text and "PRIVATE" not in response.text
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/ready").status_code == 503


def test_fallback_has_its_own_rate_limit_and_distrusts_forwarded_headers(client, db, monkeypatch):
    from app import telemetry
    warm(client, db)
    monkeypatch.setattr(catalog, "list_matches", outage)
    def unavailable(*args):
        raise JournalUnavailable("PRIVATE_STORAGE")
    monkeypatch.setattr(telemetry, "emit_event", unavailable)
    monkeypatch.setenv("RATE_PUBLIC_PER_MINUTE", "1")
    get_settings.cache_clear()
    assert client.get("/api/v1/matches", headers={"X-Real-IP": "198.51.100.1"}).status_code == 200
    response = client.get("/api/v1/matches", headers={"X-Real-IP": "198.51.100.2"})
    assert response.status_code == 429 and response.headers["Retry-After"] == "60"


def test_successful_write_invalidates_old_public_totals(client, db):
    warm(client, db)
    @client.app.post("/api/v1/test-write")
    def write():
        return {"done": True}
    assert client.app.state.public_snapshots.entries
    assert client.post("/api/v1/test-write").status_code == 200
    assert not client.app.state.public_snapshots.entries


def test_audit_startup_failure_allows_health_but_not_mutation(monkeypatch):
    from app import main, telemetry
    from fastapi.testclient import TestClient
    def unavailable(*args):
        raise JournalUnavailable("PRIVATE_STORAGE")
    monkeypatch.setattr(main, "get_journal", unavailable)
    monkeypatch.setattr(telemetry, "emit_event", unavailable)
    app = main.create_app()
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.post("/api/v1/auth/session").status_code == 503


def test_partial_public_response_is_replaced_before_any_bytes_are_sent():
    store = PublicSnapshots()
    store.put(("/api/v1/matches", b""), b'{"items":[{"bettingOpen":true,"dataIssues":[]}]}')
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"PRIVATE_PARTIAL", "more_body": True})
        raise RuntimeError("PRIVATE_FAILURE")
    async def drive():
        sent = []
        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(message):
            sent.append(message)
        scope = {"type": "http", "method": "GET", "path": "/api/v1/matches", "headers": [],
                 "query_string": b"", "route": SimpleNamespace(path="/api/v1/matches")}
        await PublicAvailabilityMiddleware(inner, store)(scope, receive, send)
        assert sent[0]["status"] == 200
        body = b"".join(m.get("body", b"") for m in sent)
        assert b"PRIVATE" not in body and json.loads(body)["items"][0]["bettingOpen"] is False
    asyncio.run(drive())


def test_snapshot_count_and_body_size_are_bounded():
    store = PublicSnapshots()
    for i in range(MAX_ENTRIES + 10):
        store.put(("/api/v1/matches", str(i).encode()), b"[]")
    assert len(store.entries) == MAX_ENTRIES
    assert ("/api/v1/matches", b"0") not in store.entries
    store.put(("/api/v1/matches", b"big"), b" " * 300000)
    assert ("/api/v1/matches", b"big") not in store.entries


def test_buffered_response_send_failure_is_logged_as_incomplete():
    from app.telemetry import AuditMiddleware
    from app.audit_journal import get_journal
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"[]", "more_body": False})
    async def drive():
        async def receive():
            return {"type": "http.request", "body": b""}
        async def lost_connection(message):
            if message["type"] == "http.response.body":
                raise OSError("PRIVATE_CONNECTION")
        scope = {"type": "http", "method": "GET", "path": "/api/v1/teams", "headers": [],
                 "query_string": b"", "route": SimpleNamespace(path="/api/v1/teams")}
        with pytest.raises(OSError):
            await PublicAvailabilityMiddleware(AuditMiddleware(inner), PublicSnapshots())(scope, receive, lost_connection)
    asyncio.run(drive())
    events = [json.loads(e) for e in get_journal().export()]
    assert events[-1]["response_complete"] is False and events[-1]["outcome"] == "failure"
    assert events[-1]["reason"] == "response_incomplete"
