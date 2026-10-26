import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { createServer } from 'node:http';
import { test } from 'node:test';

const source = readFileSync(new URL('../docs/frontend-api.ts', import.meta.url), 'utf8');
const js = stripTypeScriptTypes(source);
const { createLoLPickApi, LoLPickApiError, createAccountResponseGuard } = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
const root = 'http://127.0.0.1:8000/api/v1';
const health = {status:'ok',service:'lolpick-backend',version:'0.14.1'};
const member = {memberId:'ac1691c5-817a-41c8-85f9-42b1a30dd101',currentPoints:1000,currentSeasonId:'s'};
const key = 'dfe7600e-8a66-4e30-a89e-75fd54d9a4dd';
const make = fetcher => createLoLPickApi({ apiRoot: root, getAccessToken: () => 'test-token', fetcher });

test('HTML 503 preserves status, request ID and Retry-After', async () => {
  const api = make(async () => new Response('<html>proxy unavailable</html>', {
    status: 503, headers: { 'X-Request-ID': 'proxy-1', 'Retry-After': '30' }
  }));
  await assert.rejects(api.health(), e => e instanceof LoLPickApiError && e.status === 503 &&
    e.code === 'http_error' && e.requestId === 'proxy-1' && e.retryAfter === '30' && !e.message.includes('<html>'));
});
test('empty 429 preserves throttling instructions', async () => {
  await assert.rejects(make(async () => new Response('', {status: 429, headers: {'Retry-After':'10'}})).health(),
    e => e.status === 429 && e.retryAfter === '10');
});
test('JSON business error keeps its code and message', async () => {
  await assert.rejects(make(async () => Response.json({detail:{code:'betting_closed',message:'Closed'}}, {status:409})).health(),
    e => e.status === 409 && e.code === 'betting_closed' && e.message === 'Closed');
});
test('HTML 200 is rejected as invalid response', async () => {
  await assert.rejects(make(async () => new Response('<html>wrong origin</html>')).health(),
    e => e.status === 200 && e.code === 'invalid_response');
});
test('a failed error-body read still preserves HTTP status', async () => {
  const response = {status:503, ok:false, headers:new Headers({'X-Request-ID':'body-1'}),
    text:async () => {throw new TypeError('stream failure');}};
  await assert.rejects(make(async () => response).health(), e => e.status === 503 && e.requestId === 'body-1');
});
test('a failed success-body read indicates unknown write result', async () => {
  let requests = 0;
  const response = {status:200, ok:true, headers:new Headers(),
    text:async () => {throw new TypeError('stream failure');}};
  await assert.rejects(make(async () => {requests++; return response;}).placeBet({matchId:'m',teamId:'A',amount:100},key),
    e => e.code === 'response_body_unavailable');
  assert.equal(requests, 1);
});
test('JSON null cheer and empty 204 logout remain supported', async () => {
  assert.equal(await make(async () => Response.json(null)).cheer('s'), null);
  assert.equal(await make(async () => new Response(null, {status:204})).logout(), undefined);
});
test('public duplicate requests share only the in-flight fetch', async () => {
  let resolve, requests = 0;
  const api = make(() => {requests++; return new Promise(r => {resolve = r;});});
  const first = api.health(), second = api.health();
  assert.equal(requests, 1);
  resolve(Response.json(health));
  assert.deepEqual(await first, await second);
  const third = api.health(); assert.equal(requests, 2);
  resolve(Response.json(health)); await third;
});
test('same POST retry preserves payload and key without automatic retries', async () => {
  const calls = [], payload = {matchId:'m',teamId:'A',amount:100};
  const api = make(async (url, init) => {calls.push({url,init}); throw new TypeError('connection lost');});
  await assert.rejects(api.placeBet(payload,key));
  assert.equal(calls.length, 1);
  await assert.rejects(api.placeBet(payload,key));
  assert.equal(calls[0].init.body,calls[1].init.body);
  assert.equal(calls[0].init.headers['Idempotency-Key'],key);
  assert.equal(calls[1].init.headers['Idempotency-Key'],key);
});
test('public GET sends no token; private GET retrieves current token each time', async () => {
  let token = 'account-one'; const calls = [];
  const api = createLoLPickApi({apiRoot:root,getAccessToken:()=>token,fetcher:async (url,init)=>{
    calls.push(init); return Response.json(url.endsWith('/health') ? health : member);
  }});
  await api.health(); await api.me('s'); token = 'account-two'; await api.me('s');
  assert.equal(calls[0].headers.Authorization, undefined);
  assert.equal(calls[1].headers.Authorization, 'Bearer account-one');
  assert.equal(calls[2].headers.Authorization, 'Bearer account-two');
});
test('incorrect root is rejected before sending credentials', () => {
  for (const apiRoot of ['https://example.com','https://example.com/api/v10','https://example.com/api/v1?token=x',
    'http://example.com/api/v1','https://user:password@example.com/api/v1'])
    assert.throws(() => createLoLPickApi({apiRoot}));
  assert.doesNotThrow(() => createLoLPickApi({apiRoot:'https://example.com/backend/api/v1/'}));
});
test('real loopback HTTP keeps proxy errors and performs each request once', async () => {
  const seen = [];
  const server = createServer((request,response) => {
    seen.push({url:request.url,token:request.headers.authorization,key:request.headers['idempotency-key']});
    response.writeHead(503, {'Content-Type':'text/html','X-Request-ID':'tcp-proxy','Retry-After':'20'});
    response.end('<html>Service unavailable</html>');
  });
  await new Promise((resolve,reject) => {server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  try {
    const api = createLoLPickApi({apiRoot:`http://127.0.0.1:${server.address().port}/api/v1`,getAccessToken:()=> 'test-token'});
    await assert.rejects(api.health(), e => e.status === 503 && e.requestId === 'tcp-proxy' && e.retryAfter === '20');
    await assert.rejects(api.placeBet({matchId:'m',teamId:'A',amount:100},key), e => e.status === 503);
    assert.equal(seen.length,2);
    assert.equal(seen[0].token,undefined);
    assert.equal(seen[1].token,'Bearer test-token');
    assert.equal(seen[1].key,key);
  } finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
});

test('token wait expires before dispatch and late completion never sends POST', async () => {
  let resolveToken, requests = 0;
  const api = createLoLPickApi({apiRoot:root,tokenTimeoutMs:10,
    getAccessToken:()=>new Promise(resolve=>{resolveToken=resolve;}),
    fetcher:async()=>{requests++;return Response.json(member);}});
  await assert.rejects(api.registerSession(key),e=>e.code==='client_token_timeout'&&!e.outcomeUnknown);
  resolveToken('late-token'); await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(requests,0);
});
test('account change while obtaining token blocks a queued financial POST', async () => {
  let resolveToken, requests = 0, scope = 'account-one';
  const api = createLoLPickApi({apiRoot:root,getAuthScope:()=>scope,
    getAccessToken:()=>new Promise(resolve=>{resolveToken=resolve;}),
    fetcher:async()=>{requests++;return Response.json(member);}});
  const pending = api.placeBet({matchId:'m',teamId:'A',amount:100},key);
  await Promise.resolve(); scope='account-two'; resolveToken('account-two-token');
  await assert.rejects(pending,e=>e.code==='client_auth_changed'&&!e.outcomeUnknown);
  assert.equal(requests,0);
});
test('invalidateAuth discards a dispatched private success', async () => {
  let resolveFetch, started;
  const dispatched = new Promise(resolve=>{started=resolve;});
  const api = createLoLPickApi({apiRoot:root,getAccessToken:()=> 'one',
    fetcher:()=>{started();return new Promise(resolve=>{resolveFetch=resolve;});}});
  const pending=api.me('s'); await dispatched;
  api.invalidateAuth(); resolveFetch(Response.json(member));
  await assert.rejects(pending,e=>e.code==='client_auth_changed');
});
test('account invalidation after write dispatch preserves unknown outcome', async () => {
  let resolveFetch, started;
  const dispatched = new Promise(resolve=>{started=resolve;});
  const api=createLoLPickApi({apiRoot:root,getAccessToken:()=> 'one',
    fetcher:()=>{started();return new Promise(resolve=>{resolveFetch=resolve;});}});
  const pending=api.placeBet({matchId:'m',teamId:'A',amount:100},key);await dispatched;
  api.invalidateAuth();resolveFetch(Response.json({}));
  await assert.rejects(pending,e=>e.code==='client_auth_changed'&&e.outcomeUnknown);
});
test('submitted body is snapshotted before awaiting auth', async () => {
  let resolveToken, actualBody;
  const api=createLoLPickApi({apiRoot:root,getAccessToken:()=>new Promise(r=>{resolveToken=r;}),
    fetcher:async(url,init)=>{actualBody=init.body;return new Response('',{status:503});}});
  const body={matchId:'m',teamId:'A',amount:100};const pending=api.placeBet(body,key);
  body.amount=999;await Promise.resolve();resolveToken('one');
  await assert.rejects(pending,e=>e.status===503&&e.outcomeUnknown);
  assert.equal(JSON.parse(actualBody).amount,100);
});
test('dot segment and malformed IDs never dispatch', () => {
  let requests=0;const api=make(async()=>{requests++;return Response.json({});});
  for(const id of ['.','..','',' m1','m1\n'])assert.throws(()=>api.match(id),e=>e.code==='client_invalid_id');
  assert.equal(requests,0);
});
test('valid JSON with wrong fields cannot masquerade as success', async () => {
  for(const body of [null,{},[],{status:'ok'}, {...health,status:'wrong'}])
    await assert.rejects(make(async()=>Response.json(body)).health(),e=>e.code==='invalid_response');
  assert.deepEqual(await make(async()=>Response.json(health)).health(),health);
});
test('money beyond safe integer precision is rejected on response', async () => {
  await assert.rejects(make(async()=>Response.json({...member,currentPoints:9007199254740992})).me('s'),
    e=>e.code==='invalid_response');
});
test('stale snapshot metadata is made available without persistent cache', async () => {
  const seen=[];const api=createLoLPickApi({apiRoot:root,onPublicMetadata:m=>seen.push(m),
    fetcher:async()=>Response.json(health,{headers:{'X-Data-Stale':'1','X-Data-Age-Seconds':'42','X-Request-ID':'stale-1'}})});
  await api.health();assert.deepEqual(seen,[{path:'/health',stale:true,ageSeconds:42,requestId:'stale-1'}]);
});
test('guard drops both earlier-account results and errors at the UI boundary', async () => {
  for(const failure of [false,true]){
    const guard=createAccountResponseGuard();let settle,applied=0,reported=0;
    const pending=guard.run(()=>new Promise((resolve,reject)=>{settle=failure?reject:resolve;}),()=>applied++,()=>reported++);
    guard.changeAccount();settle(failure?new Error('old'):member);
    assert.equal((await pending).kind,'discarded');assert.equal(applied+reported,0);
  }
});
test('HTTP abort yields a typed unknown-write outcome without automatic retry', async () => {
  let requests=0;
  const api=createLoLPickApi({apiRoot:root,getAccessToken:()=> 'one',timeoutMs:10,
    fetcher:async(url,init)=>{requests++;return new Promise((resolve,reject)=>{
      init.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true});
    });}});
  await assert.rejects(api.placeBet({matchId:'m',teamId:'A',amount:100},key),e=>e.code==='client_timeout'&&e.outcomeUnknown);
  assert.equal(requests,1);
});
test('every captured success response satisfies its generated contract', async () => {
  const examples=JSON.parse(readFileSync(new URL('../tests/fixtures/api-examples.json',import.meta.url),'utf8'));
  const cases=[['health',[],'GET /api/v1/health'],['teams',['s'],'GET /api/v1/teams'],
    ['seasons',[2026],'GET /api/v1/seasons'],['events',[2026],'GET /api/v1/events'],
    ['event',['s'],'GET /api/v1/events/{event_id}'],['matches',[],'GET /api/v1/matches'],
    ['match',['m1'],'GET /api/v1/matches/{match_id}'],['stakeRanking',['m1'],'GET /api/v1/matches/{match_id}/stake-ranking'],
    ['me',['s'],'GET /api/v1/auth/me'],['cheer',['s'],'GET /api/v1/auth/cheer'],
    ['matchContext',['m1'],'GET /api/v1/auth/matches/{match_id}/context'],['bets',[],'GET /api/v1/auth/bets'],
    ['registerSession',[key],'POST /api/v1/auth/session'],['chooseCheer',[{seasonId:'s',teamId:'A'},key],'POST /api/v1/auth/cheer'],
    ['placeBet',[{matchId:'m1',teamId:'A',amount:100},key],'POST /api/v1/auth/bets']];
  for(const [method,args,operation] of cases){
    const expected=examples[operation].response;
    const api=make(async()=>Response.json(expected));
    assert.deepEqual(await api[method](...args),expected,operation);
  }
  for(const [method,args,operation] of [
    ['refresh',['s'],'POST /api/v1/admin/seasons/{season_id}/refresh'],
    ['activate',['s'],'POST /api/v1/admin/seasons/{season_id}/activate'],
    ['finish',['s'],'POST /api/v1/admin/seasons/{season_id}/finish'],
    ['result',['m1',{result:'cancelled'}],'POST /api/v1/admin/matches/{match_id}/result']]){
      const expected=examples[operation].response;const api=make(async()=>Response.json(expected));
      assert.deepEqual(await api.admin[method](...args),expected,operation);
  }
});
