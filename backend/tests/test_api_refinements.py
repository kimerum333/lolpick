"""Behavioral regressions for the frontend handoff fixes."""
from datetime import timedelta
from sqlalchemy import event, select
from app.models import Match
from app.services.teams import preserve_team
import pytest
import test_user_actions as fixtures


@pytest.fixture
def action_fixture():
    fixture = fixtures.UserActionsTests()
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


def test_all_catalog_operations_allow_anonymous_in_schema(action_fixture):
    self = action_fixture
    schema = self.client.app.openapi()
    public = [op for methods in schema['paths'].values() for op in methods.values()
              if isinstance(op, dict) and 'catalog' in op.get('tags', [])]
    self.assertEqual(len(public), 7)
    self.assertTrue(all({} in op['security'] for op in public))
    private = schema['paths']['/api/v1/auth/matches/{match_id}/context']['get']
    self.assertEqual(private['security'], [{'HTTPBearer': []}])
    self.assertEqual(self.client.get('/api/v1/events/s').status_code, 200)
    self.assertEqual(self.client.get('/api/v1/matches/m1/stake-ranking').status_code, 200)

def test_public_eligibility_and_write_agree_for_unconfirmed_source(action_fixture):
    self = action_fixture
    from app.services.catalog import get_match
    from app.services.points import apply_points, PointOperationError
    from sqlalchemy.orm import Session
    with Session(self.engine) as db, db.begin():
        match = db.scalar(select(Match).where(Match.public_code == 'm1'))
        match.provider_status = 'running'
        db.flush()
        public = get_match(db, 'm1')
        self.assertFalse(public.bettingOpen)
        self.assertEqual(public.bettingClosedReason, 'provider_data_unconfirmed')
        from app.models import Member
        member = db.scalar(select(Member).where(Member.google_sub == 'google-alice'))
        with self.assertRaises(PointOperationError) as rejected:
            apply_points(db, member_id=member.id, season_id=match.season_id, match_id=match.id,
                amount=-10, reason='bet_stake', selected_side=1, idempotency_key='closed')
        self.assertEqual(rejected.exception.code, 'betting_closed')

def test_missing_display_does_not_delete_curated_values(action_fixture):
    self = action_fixture
    from sqlalchemy.orm import Session
    with Session(self.engine) as db, db.begin():
        team = preserve_team(db, 'pandascore', {'id':'A','name':'Alpha','logo':'https://example.invalid/a.png',
            'acronym':'ALP','region':'KR'}, self.now + timedelta(minutes=1))
        expected = (team.logo_url, team.acronym, team.name, dict(team.display_data))
        team = preserve_team(db, 'pandascore', {'id':'A','logo':None,'acronym':None},
                             self.now + timedelta(minutes=2))
        self.assertEqual((team.logo_url, team.acronym, team.name, team.display_data), expected)
        from app.integrations.pandascore_mapping import normalize_match
        normalized = normalize_match({'id':77,'serie_id':700,'videogame':{'id':1,'slug':'league-of-legends'},
            'status':'not_started','opponents':[{'type':'Team','opponent':{'id':11}}]},700)
        assert 'name' not in normalized.teams[0]
        preserve_team(db,'pandascore',{'id':'11','name':'Curated'},self.now+timedelta(minutes=3))
        preserved=preserve_team(db,'pandascore',normalized.teams[0],self.now+timedelta(minutes=4))
        assert preserved.name=='Curated'

def test_history_round_trips_do_not_grow_per_item(action_fixture):
    self = action_fixture
    self.assertEqual(self.cheer().status_code, 200)
    self.assertEqual(self.bet().status_code, 200)
    counts = []
    def count(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith('SELECT'):
            counts.append(statement)
    event.listen(self.engine, 'before_cursor_execute', count)
    try:
        counts.clear()
        one = self.request('GET','bets?season_id=s')
        one_count = len(counts)
        self.assertEqual(one.status_code,200)
        self.assertEqual(self.bet(match='m2').status_code,200)
        self.assertEqual(self.bet(match='m3',team='C').status_code,200)
        counts.clear()
        three = self.request('GET','bets?season_id=s')
        self.assertEqual(three.status_code,200)
        self.assertEqual(three.json()['total'],3)
        self.assertEqual(len(counts),one_count)
        self.assertEqual(three.json()['items'][-1]['matchId'], 'm1')
    finally:
        event.remove(self.engine,'before_cursor_execute',count)

def test_frontend_can_read_retry_after_on_early_errors(action_fixture):
    self = action_fixture
    response = self.client.get('/api/v1/matches?limit=101', headers={'Origin':'http://localhost:5173'})
    self.assertEqual(response.status_code,422)
    self.assertIn('Retry-After', response.headers['access-control-expose-headers'])


def test_me_and_registration_acquire_session_before_member(action_fixture):
    from sqlalchemy.orm import Session
    from sqlalchemy.dialects import postgresql
    self = action_fixture
    statements = []
    def capture(state):
        if getattr(state.statement, '_for_update_arg', None) is not None:
            statements.append(str(state.statement.compile(dialect=postgresql.dialect())))
    event.listen(Session, 'do_orm_execute', capture)
    try:
        assert self.request('GET','me?season_id=s').status_code == 200
        first_member = next(i for i, s in enumerate(statements) if 'FROM lolpick.lolpick_members' in s)
        first_session = next(i for i, s in enumerate(statements) if 'FROM lolpick.lolpick_member_sessions' in s)
        assert first_session < first_member
        statements.clear()
        assert self.request('POST','session').status_code == 200
        first_season = next(i for i,s in enumerate(statements) if 'FROM lolpick.lolpick_seasons' in s)
        first_session = next(i for i,s in enumerate(statements) if 'FROM lolpick.lolpick_member_sessions' in s)
        first_member = next(i for i,s in enumerate(statements) if 'FROM lolpick.lolpick_members' in s)
        assert first_season < first_session < first_member
        statements.clear()
        assert self.request('GET','bets?season_id=s').status_code == 200
        assert not any('FROM lolpick.lolpick_seasons' in s for s in statements)
    finally:
        event.remove(Session, 'do_orm_execute', capture)


def test_locked_session_refreshes_previously_loaded_identity(action_fixture):
    from sqlalchemy.orm import Session
    from app.models import MemberSession
    from app.services import sessions
    self = action_fixture
    with Session(self.engine, expire_on_commit=False) as first:
        before = sessions.session_for_user(first, self.users['alice'])
        first.commit()
        assert before.status == 'active'
        with Session(self.engine) as second, second.begin():
            row = sessions.session_for_user(second, self.users['alice'])
            sessions.revoke_session(row)
        reread = sessions.session_for_user(first, self.users['alice'])
        assert reread is before
        assert reread.status == 'revoked'
        assert sessions.session_error(reread, sessions.now_utc()).code == 'session_revoked'
        assert first.scalar(select(MemberSession.status).where(MemberSession.id==reread.id)) == 'revoked'
