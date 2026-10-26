"""Isolated API regressions; unittest also runs without optional pytest tooling."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.core.config import Settings, get_settings

Settings.model_config["env_file"] = None
get_settings.cache_clear()
from app.core.auth import AuthenticatedUser, AuthError, get_authenticated_user
from app.db.session import get_db
from app.main import create_app
from app.models import Base, CheerContract, Match, Member, MemberSession, PointTransaction, Season, SystemLog
from app.services import sessions
from app.services.points import finish_season
from app.services.settlement import record_result, settle_locked_match
from app.services.teams import preserve_source, preserve_team


class UserActionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("LOLPICK_TEST_TEMP_DIR"))
        self.env = patch.dict(os.environ, {"APP_ENV": "test", "SYSTEM_LOG_ENABLED": "false",
            "LOG_DIRECTORY": self.tmp.name, "RATE_PUBLIC_PER_MINUTE": "10000",
            "RATE_AUTH_PER_MINUTE": "1000", "RATE_ADMIN_PER_MINUTE": "1000",
            "RATE_AUTH_IP_PER_MINUTE":"10000", "RATE_ADMIN_IP_PER_MINUTE":"10000",
            "RATE_MEMBER_PER_MINUTE":"10000", "RATE_SESSION_PER_MINUTE":"10000",
            "ADMIN_REQUIRE_MFA":"false"})
        self.env.start()
        get_settings.cache_clear()
        self.engine = create_engine("sqlite+pysqlite://", poolclass=StaticPool,
            connect_args={"check_same_thread": False}, execution_options={"schema_translate_map": {"lolpick": None}})
        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.now = datetime.now(timezone.utc)
        self.users = {"alice": AuthenticatedUser(uuid4(), "google-alice", uuid4(), self.now),
                      "bob": AuthenticatedUser(uuid4(), "google-bob", uuid4(), self.now)}
        with Session(self.engine) as db, db.begin():
            source = preserve_source(db, "pandascore")
            season = Season(public_code="s", slug="worlds", year=2026, name="Test", status="active",
                            initial_points=1000, is_demo=False)
            db.add(season)
            db.flush()
            teams = [preserve_team(db, "pandascore", {"id": name, "name": name}, self.now) for name in "ABCD"]
            for i, pair in enumerate(((0, 1), (0, 1), (2, 3)), 1):
                db.add(Match(public_code=f"m{i}", season_id=season.id, api_source_id=source.id,
                    external_match_id=str(i), round="Final", team_a_id=teams[pair[0]].id,
                    team_b_id=teams[pair[1]].id, starts_at=self.now+timedelta(hours=2), status="scheduled",
                    provider_status="not_started", api_synced_at=self.now, settlement_status="pending", is_demo=False))
            for name, user in self.users.items():
                member = Member(auth_user_id=user.user_id, google_sub=user.google_sub)
                db.add(member)
                db.flush()
                db.add(MemberSession(auth_session_id=user.session_id, member_id=member.id, create_dt=self.now,
                    expires_at=self.now+timedelta(hours=6), status="active", status_changed_at=self.now))
        app = create_app()
        def database():
            with Session(self.engine, expire_on_commit=False) as db:
                yield db
        def identity(request: Request):
            user = self.users.get(request.headers.get("authorization", "").removeprefix("Bearer "))
            if user is None:
                raise AuthError(401, "invalid_token", "Invalid test token.")
            return user
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_authenticated_user] = identity
        self.native = patch.object(sessions, "native_session_created", return_value=self.now)
        self.native.start()
        self.client = TestClient(app)  # No live DB or background replay worker.

    def tearDown(self):
        self.client.close()
        self.native.stop()
        from app.audit_journal import _journal
        import logging
        logger = logging.getLogger("lolpick.http_audit")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        _journal.cache_clear()
        self.engine.dispose()
        self.env.stop()
        get_settings.cache_clear()
        self.tmp.cleanup()

    def request(self, method, path, *, user="alice", key=None, **kwargs):
        headers = {"Authorization": f"Bearer {user}"} if user else {}
        if method == "POST":
            headers["Idempotency-Key"] = str(key or uuid4())
        if "content" in kwargs:
            headers["Content-Type"] = "application/json"
        return self.client.request(method, "/api/v1/auth/"+path, headers=headers, **kwargs)

    def cheer(self, team="A", **kwargs):
        return self.request("POST", "cheer", json={"seasonId": "s", "teamId": team}, **kwargs)

    def bet(self, team="A", amount=100, match="m1", **kwargs):
        return self.request("POST", "bets", json={"matchId": match, "teamId": team, "amount": amount}, **kwargs)

    def scalar(self, statement):
        with Session(self.engine) as db:
            return db.scalar(statement)

    def count(self, model):
        return self.scalar(select(func.count()).select_from(model))

    def mutate(self, model, condition, **values):
        with Session(self.engine) as db, db.begin():
            row = db.scalar(select(model).where(condition))
            for key, value in values.items():
                setattr(row, key, value)

    def test_initial_null_then_choose_and_bet(self):
        self.assertIsNone(self.request("GET", "cheer?season_id=s").json())
        self.assertTrue(self.cheer().json()["sealHeld"])
        response = self.bet()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["cheerAction"], "cheering")
        self.assertEqual(self.scalar(select(Member.current_points).where(Member.google_sub == "google-alice")), 900)
        self.assertEqual(self.count(PointTransaction), 2)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_contract_required(self):
        response = self.bet()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "cheer_required")
        self.assertEqual(self.count(PointTransaction), 0)

    def test_cheer_cannot_change_even_before_betting(self):
        self.cheer()
        self.assertEqual(self.cheer("B").json()["detail"]["code"], "cheer_team_locked")
        self.assertEqual(self.count(CheerContract), 1)

    def test_cheer_idempotency_and_payload_conflict(self):
        key = uuid4()
        first = self.cheer(key=key)
        self.assertEqual(self.cheer(key=key).json(), first.json())
        self.assertEqual(self.cheer("B", key=key).json()["detail"]["code"], "idempotency_conflict")
        self.assertEqual(self.count(CheerContract), 1)

    def test_invalid_cheer_team(self):
        self.assertEqual(self.cheer("unknown").status_code, 422)
        self.assertEqual(self.count(CheerContract), 0)

    def test_upcoming_cheer_and_finished_cheer(self):
        self.mutate(Season, Season.public_code == "s", status="upcoming")
        self.assertEqual(self.cheer().status_code, 200)
        self.assertEqual(self.bet().json()["detail"]["code"], "season_not_active")
        self.mutate(Season, Season.public_code == "s", status="finished")
        self.assertEqual(self.cheer(user="bob").json()["detail"]["code"], "cheer_closed")

    def test_bet_replay_after_close(self):
        self.cheer()
        key = uuid4()
        first = self.bet(key=key)
        self.mutate(Match, Match.public_code == "m1", starts_at=self.now-timedelta(seconds=1))
        self.assertEqual(self.bet(key=key).json(), first.json())
        self.assertEqual(self.count(PointTransaction), 2)

    def test_bet_key_different_payload(self):
        self.cheer()
        key = uuid4()
        self.bet(key=key)
        for kwargs in ({"amount": 101}, {"team": "B"}, {"match": "m2"}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.bet(key=key, **kwargs).json()["detail"]["code"], "idempotency_conflict")
        self.assertEqual(self.count(PointTransaction), 2)

    def test_new_key_cannot_add_second_stake(self):
        self.cheer()
        self.bet()
        self.assertEqual(self.bet().json()["detail"]["code"], "bet_already_placed")
        self.assertEqual(self.count(PointTransaction), 2)

    def test_failed_betrayal_keeps_seal_and_balance(self):
        self.cheer()
        response = self.bet("B", amount=1001)
        self.assertEqual(response.json()["detail"]["code"], "point_balance_limit")
        self.assertTrue(self.request("GET", "cheer?season_id=s").json()["sealHeld"])
        self.assertEqual(self.count(PointTransaction), 0)

    def test_betrayal_and_later_cheering_do_not_restore_seal(self):
        self.cheer()
        self.assertEqual(self.bet("B").json()["cheerAction"], "betrayal")
        self.bet("A", match="m2")
        history = self.request("GET", "cheer?season_id=s").json()
        self.assertFalse(history["sealHeld"])
        self.assertEqual((history["cheeringCount"], history["betrayalCount"]), (1, 1))

    def test_unrelated_match_is_ordinary_bet(self):
        self.cheer()
        self.assertIsNone(self.bet("C", match="m3").json()["cheerAction"])
        self.assertTrue(self.request("GET", "cheer?season_id=s").json()["sealHeld"])
        self.assertEqual(self.scalar(select(PointTransaction.contract_id).where(PointTransaction.reason == "bet_stake")), None)

    def test_cancel_refund_keeps_betrayal_and_finish_finalizes(self):
        self.cheer()
        stake_id = self.bet("B").json()["id"]
        with Session(self.engine) as db, db.begin():
            season = db.scalar(select(Season))
            for match in db.scalars(select(Match)):
                record_result(match, "cancelled")
                db.flush()
                settle_locked_match(db, match, season)
            finish_season(db, "s")
        history = self.request("GET", "cheer?season_id=s").json()
        self.assertFalse(history["sealHeld"])
        self.assertIsNotNone(history["finalizedAt"])
        self.assertEqual(history["betrayalCount"], 1)
        bets = self.request("GET", "bets").json()["items"]
        self.assertEqual((bets[0]["id"], bets[0]["status"], bets[0]["payout"]), (stake_id, "refunded", 100))
        self.assertEqual(self.scalar(select(Member.current_points).where(Member.google_sub == "google-alice")), 1000)

    def test_private_history_isolates_users(self):
        self.cheer()
        self.bet()
        self.assertEqual(self.request("GET", "bets", user="bob").json()["total"], 0)
        self.assertIsNone(self.request("GET", "cheer?season_id=s", user="bob").json())
        self.assertEqual(self.request("GET", "bets?member_id=1", user="bob").status_code, 422)

    def test_history_pagination(self):
        self.cheer()
        self.bet()
        self.bet(match="m2")
        data = self.request("GET", "bets?season_id=s&limit=1&offset=1").json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["items"][0]["matchId"], "m1")

    def test_revoked_session_blocked(self):
        self.cheer()
        self.assertEqual(self.request("POST", "logout").status_code, 204)
        for method, path in (("GET", "bets"), ("GET", "cheer?season_id=s")):
            self.assertEqual(self.request(method, path).json()["detail"]["code"], "session_revoked")
        self.assertEqual(self.bet().status_code, 401)
        self.assertEqual(self.cheer().status_code, 401)
        self.assertEqual(self.count(PointTransaction), 0)

    def test_expired_session_blocked_and_persisted(self):
        self.mutate(MemberSession, MemberSession.auth_session_id == self.users["alice"].session_id,
            create_dt=self.now-timedelta(hours=7), expires_at=self.now-timedelta(hours=1))
        self.assertEqual(self.cheer().json()["detail"]["code"], "session_expired")
        self.assertEqual(self.scalar(select(MemberSession.status).where(
            MemberSession.auth_session_id == self.users["alice"].session_id)), "expired")

    def test_missing_native_session_blocked(self):
        with patch.object(sessions, "native_session_created", side_effect=AuthError(401, "upstream_session_missing", "Login again.")):
            self.assertEqual(self.cheer().status_code, 401)
        self.assertEqual(self.count(CheerContract), 0)

    def test_unauthenticated_blocked(self):
        self.assertEqual(self.cheer(user=None).status_code, 401)
        self.assertEqual(self.request("GET", "bets", user=None).status_code, 401)

    def test_strict_amounts_and_forged_fields(self):
        self.cheer()
        for amount in (True, 1.0, "100", 0, -1, 9007199254740992):
            with self.subTest(amount=amount):
                self.assertEqual(self.bet(amount=amount).status_code, 422)
        for field in ("memberId", "selectedSide", "contractId", "cheerAction"):
            with self.subTest(field=field):
                self.assertEqual(self.request("POST", "bets", json={"matchId": "m1", "teamId": "A", "amount": 100,
                    field: "forged"}).status_code, 422)
        self.assertEqual(self.count(PointTransaction), 0)

    def test_unknown_duplicate_query_and_json(self):
        self.assertEqual(self.request("GET", "bets?limit=1&limit=2").status_code, 422)
        self.assertEqual(self.request("GET", "cheer?season_id=s&unknown=x").status_code, 422)
        self.assertEqual(self.request("POST", "cheer", content='{"seasonId":"s","teamId":"A","teamId":"B"}',
            ).status_code, 422)

    def test_ineligible_started_and_stale_matches(self):
        self.cheer()
        self.mutate(Match, Match.public_code == "m1", starts_at=self.now-timedelta(seconds=1))
        self.assertEqual(self.bet().json()["detail"]["code"], "betting_closed")
        self.mutate(Match, Match.public_code == "m1", starts_at=self.now+timedelta(hours=1),
            api_synced_at=self.now-timedelta(days=1))
        self.assertEqual(self.bet().json()["detail"]["code"], "provider_data_stale")
        self.assertEqual(self.count(PointTransaction), 0)

    def test_audit_failure_rolls_back_financial_and_seal_changes(self):
        self.cheer()
        with patch("app.services.user_actions.record_event", side_effect=RuntimeError("Test failure")):
            self.assertEqual(self.bet("B").status_code, 500)
        self.assertEqual(self.count(PointTransaction), 0)
        self.assertTrue(self.request("GET", "cheer?season_id=s").json()["sealHeld"])


if __name__ == "__main__":
    unittest.main()
