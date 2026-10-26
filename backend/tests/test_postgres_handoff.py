"""Native lock regressions. Run only via the guarded disposable PG fixture."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
import pytest
from alembic import command
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from app.core.auth import AuthenticatedUser
from app.models import Member, MemberSession, PointTransaction, Season
from app.services import sessions
from app.services.members import synchronize_member
from app.services.season_locks import registration_identity, registration_seasons
from app.services.user_actions import locked_user
from app.services.settlement import process_result
from test_postgres import postgres_database  # noqa: F401
from compact_fixtures import make_match


@pytest.fixture
def state(postgres_database):
    engine, config = postgres_database
    command.upgrade(config, 'head')
    now = datetime.now(timezone.utc)
    user = AuthenticatedUser(uuid4(), 'handoff-google-synthetic', uuid4(), now)
    with Session(engine) as db, db.begin():
        for code in ('a','b'):
            db.add(Season(public_code=code,slug=code,year=2026,name=code,status='active',initial_points=1000))
        db.flush()
        member=synchronize_member(db,user,operation_id=uuid4())
        db.add(MemberSession(auth_session_id=user.session_id,member_id=member.id))
        db.add(make_match(db,id='mb',season_id='b',api_source='test',external_match_id='mb',
            round='Final',team_a_id='A',team_b_id='B',status='scheduled',starts_at=now+timedelta(hours=1),
            api_data={'normalized':{'source_status':'not_started','issues':[]}}))
    return engine,user,now


@pytest.mark.postgres
def test_registration_and_me_finish_without_deadlock_or_extension(state):
    from app.api.auth import member_output
    engine,user,now=state
    gate=Barrier(2)
    with Session(engine) as db:
        expiry=sessions.session_for_user(db,user).expires_at
    def register():
        gate.wait(timeout=5)
        with Session(engine) as db,db.begin():
            registration_seasons(db)
            registration_identity(db,user)
            row=sessions.session_for_user(db,user)
            member=synchronize_member(db,user,operation_id=uuid4())
            sessions.register_session(db,user,row=row,native_created=now)
            return member_output(db,member,user,'b').currentPoints
    def me():
        gate.wait(timeout=5)
        with Session(engine) as db,db.begin():
            member=locked_user(db,user)
            return member_output(db,member,user,'b').currentPoints
    with patch.object(sessions,'native_session_created',return_value=now):
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(register),pool.submit(me)]
            assert [job.result(timeout=10) for job in jobs]==[1000,1000]
    with Session(engine) as db:
        assert sessions.session_for_user(db,user).expires_at==expiry
        assert db.scalar(select(func.count()).select_from(PointTransaction).where(
            PointTransaction.reason=='season_open'))==2
        assert db.scalar(select(func.count()).select_from(Member))==1


@pytest.mark.postgres
def test_result_in_b_does_not_wait_for_exclusive_lock_in_a(state):
    engine,user,now=state
    with Session(engine) as held, held.begin():
        held.scalar(select(Season).where(Season.public_code=='a').with_for_update())
        def finish_b():
            with Session(engine) as db,db.begin():
                db.execute(text("SET LOCAL lock_timeout = '1500ms'"))
            return process_result(db,'mb','cancelled')
        with ThreadPoolExecutor(max_workers=1) as pool:
            result=pool.submit(finish_b).result(timeout=3)
            assert result['seasonId']=='b' and result['settlementStatus']=='refunded'
