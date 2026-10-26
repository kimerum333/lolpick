from compact_fixtures import internal, lookup
from app.models import Season, Member, MemberSession
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from app.models import Base
from app.services.teams import preserve_team, preserve_source
from app.services.points import activate_season, finish_season, PointOperationError


def test_every_application_table_and_fk_has_prefix():
    assert len(Base.metadata.tables) == 9
    for table in Base.metadata.tables.values():
        assert table.name.startswith("lolpick_")
        assert all((fk.column.table.name.startswith("lolpick_") for fk in table.foreign_keys))


def test_team_rename_preserves_identity_and_current_display(db):
    now = datetime.now(timezone.utc)
    row = preserve_team(db, "pandascore", {"id": "1", "name": "Old"}, now)
    identity = row.id
    db.commit()
    row = preserve_team(db, "pandascore", {"id": "1", "name": "New"}, now + timedelta(hours=1))
    assert row.id == identity and row.name == "New"
    stale = preserve_team(db, "pandascore", {"id": "1", "name": "Old"}, now)
    assert stale.id == identity and stale.name == "New"
    other = preserve_team(db, "other", {"id": "1", "name": "Other"}, now)
    assert other.id != identity


def test_activation_does_not_finish_active_season_implicitly(db):
    db.add_all(
        [
            Season(public_code="s1", slug="test", year=2025, name="Old", label="Old", status="active"),
            Season(public_code="s2", slug="test", year=2026, name="New", label="New", status="upcoming"),
        ]
    )
    db.commit()
    activate_season(db, "s2")
    db.commit()
    assert lookup(db, Season, "s1").status == "active"
    db.commit()
    with db.begin():
        finish_season(db, "s1")
        activate_season(db, "s2")
    assert lookup(db, Season, "s2").status == "active"


def test_admin_routes_reject_anonymous_and_unprivileged_sessions(client, db, monkeypatch):
    from app.api.auth import require_server_session
    from app.core.auth import AuthenticatedUser

    for action in ("refresh", "finish", "activate"):
        assert client.post(f"/api/v1/admin/seasons/test/{action}").status_code == 401
    user = AuthenticatedUser(uuid4(), "admin-test", aal="aal2")
    db.add(Member(auth_user_id=user.user_id, google_sub=user.google_sub))
    db.commit()
    client.app.dependency_overrides[require_server_session] = lambda: user
    for action in ("refresh", "finish", "activate"):
        assert client.post(f"/api/v1/admin/seasons/test/{action}").status_code == 403
    lookup(db, Member, user.user_id).type = 2
    db.commit()
    assert client.post("/api/v1/admin/seasons/test/refresh").status_code == 409
    assert client.post("/api/v1/admin/seasons/test/finish").status_code == 409


def test_manual_refresh_uses_server_bound_series_without_changing_worker_schedule(client, db, monkeypatch):
    from app.api.auth import require_server_session
    from app.api import admin
    from app.core.auth import AuthenticatedUser

    user = AuthenticatedUser(uuid4(), "refresh-admin", aal="aal2")
    db.add_all(
        [
            Member(auth_user_id=user.user_id, google_sub=user.google_sub),
            Season(
                public_code="s1",
                slug="test",
                year=2026,
                name="Test",
                label="Test",
                status="upcoming",
                api_source_id=preserve_source(db, "pandascore").id,
                external_series_id="700",
            ),
        ]
    )
    db.flush()
    lookup(db, Member, user.user_id).type = 2
    db.commit()
    client.app.dependency_overrides[require_server_session] = lambda: user
    called = []

    class Provider:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(admin, "PandaScoreClient", Provider)
    monkeypatch.setattr(
        admin, "collect", lambda client, sid: (called.append(sid) or {}, [], datetime.now(timezone.utc))
    )

    def sync(db, **kwargs):
        called.append(kwargs)
        # Match the real synchronize response contract, not a partial stub.
        return {"seasonId": kwargs["season_id"], "seriesId": 700,
                "received": 1, "created": 0, "updated": 1, "skipped_tbd": 0,
                "stored_tbd": 0, "stored_unscheduled": 0, "skipped_unscheduled": 0,
                "conflicts": 0, "stale": 0, "settled": 0, "refunded": 0,
                "settlement_blocked": 0}

    monkeypatch.setattr(admin, "synchronize", sync)
    assert client.post("/api/v1/admin/seasons/s1/refresh", json={"series_id": 999}).status_code == 422
    assert called == []
    assert client.post("/api/v1/admin/seasons/s1/refresh").status_code == 200
    assert called[0] == 700 and called[1]["actor_member_id"] == internal(db, Member, user.user_id)
    assert called[1]["request_id"] is not None


def test_public_catalog_records_verified_actor_when_bearer_is_present(client, db, monkeypatch):
    from app.api import auth
    from app.core.auth import AuthenticatedUser
    from app.services import sessions
    from app import telemetry
    from app.core.config import get_settings

    now = datetime.now(timezone.utc)
    user = AuthenticatedUser(uuid4(), "public-actor", uuid4(), now)
    db.add(Member(auth_user_id=user.user_id, google_sub=user.google_sub))
    db.flush()
    db.add(
        MemberSession(
            auth_session_id=user.session_id,
            member_id=internal(db, Member, user.user_id),
            create_dt=now,
            expires_at=now + timedelta(hours=6),
            status="active",
            status_changed_at=now,
        )
    )
    db.commit()
    monkeypatch.setattr(auth, "get_authenticated_user", lambda credentials: user)
    monkeypatch.setattr(sessions, "native_session_created", lambda db, user: now)
    events = []
    monkeypatch.setenv("SYSTEM_LOG_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(
        telemetry,
        "mirror_event",
        lambda event: events.append(event.copy()) if event.get("event_name") == "http.request" else None,
    )
    assert client.get("/api/v1/matches", headers={"Authorization": "Bearer test"}).status_code == 200
    assert events[-1]["actor_member_id"] == str(internal(db, Member, user.user_id))
    assert client.get("/api/v1/matches").status_code == 200
    assert events[-1]["actor_member_id"] is None
