"""Short localhost HTTP smoke check using synthetic identities/data only.

Never reads .env or calls Supabase/PandaScore. The normal runtime remains
PostgreSQL-only; test dependency overrides live solely in this maintenance tool.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import threading
from datetime import timedelta
from time import monotonic
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
os.environ['APP_ENV'] = 'test'
import httpx
import uvicorn
from sqlalchemy import select
from sqlalchemy.orm import Session
import test_user_actions as test_support
from app.models import Member, Season, Match
from app.integrations.pandascore_mapping import normalize_match, normalize_series
from app.schemas.system import ErrorOut


def check(output):
    output = Path(output).resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('Save verification output outside backend source.')
    output.mkdir(parents=True, exist_ok=True)
    support = test_support.UserActionsTests()
    support.setUp()
    app = support.client.app
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=0,
        lifespan='off', log_level='critical', access_log=False, proxy_headers=False))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(8)
    port = listener.getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={'sockets':[listener]}, daemon=True)
    examples, requests = {}, []
    try:
        thread.start()
        ready = threading.Event()
        deadline = monotonic() + 5
        while not server.started:
            if not thread.is_alive() or monotonic() >= deadline:
                raise RuntimeError('Isolated HTTP listener did not start.')
            ready.wait(0.01)
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=5, trust_env=False) as client:
            def request(method, path, template=None, *, user=None, body=None, expected=200, key=None):
                headers = {'Origin':'http://localhost:5173'}
                if user:
                    headers['Authorization'] = 'Bearer ' + user
                if key:
                    headers['Idempotency-Key'] = str(key)
                kwargs = {'headers':headers}
                if body is not None:
                    kwargs['json'] = body
                response = client.request(method, '/api/v1' + path, **kwargs)
                assert response.status_code == expected, (method, path, response.status_code, response.text)
                payload = response.json() if response.content else None
                assert 'x-request-id' in response.headers
                assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'
                if user:
                    assert response.headers['cache-control'] == 'no-store'
                if expected >= 400:
                    ErrorOut.model_validate(payload)
                requests.append({'method':method,'path':path,'status':response.status_code})
                if template:
                    examples[method+' /api/v1'+template] = {'path':path,'body':body,
                        'status':response.status_code,'response':payload,
                        'responseHeaders':{name:response.headers[name] for name in
                            ('cache-control','x-data-stale','x-data-age-seconds','retry-after') if name in response.headers}}
                return response
            request('GET','/health','/health')
            # No PostgreSQL configured here: real readiness rejection, never a simulated success.
            request('GET','/ready','/ready',expected=503)
            request('POST','/auth/session','/auth/session',user='alice',key=uuid4())
            request('GET','/auth/me?season_id=s','/auth/me',user='alice')
            request('GET','/auth/cheer?season_id=s',user='alice')
            request('POST','/auth/cheer','/auth/cheer',user='alice',body={'seasonId':'s','teamId':'A'},key=uuid4())
            request('GET','/auth/cheer?season_id=s','/auth/cheer',user='alice')
            key=uuid4()
            body={'matchId':'m1','teamId':'A','amount':100}
            placed=request('POST','/auth/bets','/auth/bets',user='alice',body=body,key=key)
            retried=request('POST','/auth/bets',user='alice',body=body,key=key)
            assert retried.json()['id'] == placed.json()['id']
            request('GET','/auth/bets?season_id=s&limit=50&offset=0','/auth/bets',user='alice')
            request('GET','/auth/matches/m1/context','/auth/matches/{match_id}/context',user='alice')
            for path, template in [('/teams?season_id=s','/teams'),('/seasons?year=2026','/seasons'),
                ('/events?year=2026','/events'),('/events/s','/events/{event_id}'),
                ('/matches?season_id=s&limit=50&offset=0','/matches'),('/matches/m1','/matches/{match_id}'),
                ('/matches/m1/stake-ranking','/matches/{match_id}/stake-ranking')]:
                request('GET',path,template)
            request('GET','/matches?limit=101',expected=422)
            request('GET','/auth/me',expected=401)
            request('POST','/admin/seasons/s/finish',user='alice',expected=403)
            support.mutate(Member,Member.google_sub=='google-alice',type=2)
            source=support.scalar(select(Match.api_source_id).where(Match.public_code=='m1'))
            support.mutate(Season,Season.public_code=='s',external_series_id='700',api_source_id=source)
            game={'id':1,'slug':'league-of-legends'}
            series=normalize_series({'id':700,'league_id':50,'year':2026,'full_name':'Synthetic','videogame':game},700)
            fixture=normalize_match({'id':901,'serie_id':700,'videogame':game,'status':'not_started',
                'scheduled_at':(support.now+timedelta(hours=3)).isoformat(),
                'opponents':[{'type':'Team','opponent':{'id':i,'name':f'Test {i}'}} for i in (11,22)]},700)
            class Provider:
                def __enter__(self): return self
                def __exit__(self,*args): pass
            with patch('app.api.admin.PandaScoreClient',Provider), patch('app.api.admin.collect',
                return_value=(series,[fixture],support.now+timedelta(seconds=1))):
                request('POST','/admin/seasons/s/refresh','/admin/seasons/{season_id}/refresh',user='alice')
            request('POST','/admin/matches/m1/result','/admin/matches/{match_id}/result',
                user='alice',body={'result':'team_a_win','score_a':2,'score_b':1})
            for identity in ('m2','m3','pandascore-901'):
                request('POST',f'/admin/matches/{identity}/result',user='alice',body={'result':'cancelled'})
            request('POST','/admin/seasons/s/finish','/admin/seasons/{season_id}/finish',user='alice')
            with Session(support.engine) as db, db.begin():
                db.add(Season(public_code='next',slug='next',year=2026,name='Next',status='upcoming',initial_points=1000))
            request('POST','/admin/seasons/next/activate','/admin/seasons/{season_id}/activate',user='alice')
            request('POST','/auth/logout','/auth/logout',user='alice',expected=204)
            request('GET','/auth/me',user='alice',expected=401)
        operations={method.upper()+' '+path for path, methods in app.openapi()['paths'].items()
                    for method in methods if method in {'get','post'}}
        assert set(examples)==operations, operations-set(examples)
        (output/'api-examples.json').write_text(json.dumps(examples,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        report={'transport':'Actual TCP HTTP on 127.0.0.1 with uvicorn/httpx','operations':len(operations),
            'requests':requests,'database':'Disposable in-memory SQLite','auth':'Synthetic Google/native-session override',
            'readiness':'Expected 503, real PostgreSQL readiness not covered','live_DB_and_collection_changed':False,
            'external_requests':0,'passed':True}
        (output/'http-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return report
    finally:
        server.should_exit=True
        thread.join(timeout=5)
        listener.close()
        support.tearDown()
        if thread.is_alive():
            raise RuntimeError('Isolated HTTP listener did not stop.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    result=check(args.output)
    print(json.dumps({'passed':result['passed'],'operations':result['operations'],'requests':len(result['requests'])}))
