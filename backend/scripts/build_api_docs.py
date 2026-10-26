"""Generate a standalone frontend API guide/types from the real app schema."""
import argparse
import base64
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def ts_type(schema):
    if '$ref' in schema:
        return schema['$ref'].split('/')[-1]
    if 'const' in schema:
        return json.dumps(schema['const'], ensure_ascii=False)
    if 'enum' in schema:
        return ' | '.join(json.dumps(v, ensure_ascii=False) for v in schema['enum'])
    if 'anyOf' in schema or 'oneOf' in schema:
        return ' | '.join(ts_type(v) for v in schema.get('anyOf', schema.get('oneOf')))
    if 'allOf' in schema:
        return ' & '.join(ts_type(v) for v in schema['allOf'])
    kind = schema.get('type')
    if kind == 'array':
        if 'prefixItems' in schema:
            return '[' + ', '.join(ts_type(v) for v in schema['prefixItems']) + ']'
        return '(' + ts_type(schema.get('items', {})) + ')[]'
    if kind == 'object':
        if 'properties' not in schema:
            extra = schema.get('additionalProperties', {})
            return 'Record<string, ' + (ts_type(extra) if isinstance(extra, dict) else 'unknown') + '>'
        required = schema.get('required', [])
        return '{\n' + ''.join('  ' + key + (':' if key in required else '?:') + ' ' + ts_type(value) + ';\n'
                              for key, value in schema['properties'].items()) + '}'
    return {'integer':'number', 'number':'number','string':'string','boolean':'boolean','null':'null'}.get(kind,'unknown')


NOTES = {
    'GET /api/v1/health': '프로세스 생존 확인. DB·외부 인증·수집기 성공을 의미하지 않음.',
    'GET /api/v1/ready': '현재 DB revision·역할·보안 정책 확인. 준비되지 않으면 503. 문서 예제는 SQLite 검증 환경의 실제 거절 응답.',
    'GET /api/v1/teams': '시즌 미지정 시 모든 active 시즌의 팀 합집합. 모르는 시즌은 빈 배열. 응원 선택에는 시즌을 명시.',
    'GET /api/v1/seasons': '전체 시즌 배열. /events와 동일한 목록 계약, year 필터 가능.',
    'GET /api/v1/events': '전체 시즌을 이벤트 목록으로 반환. 동시 활성 시즌 모두 포함. startsAt/endsAt 미정 가능.',
    'GET /api/v1/events/{event_id}': '현재 event_id는 season public_code. stages는 round 집계이며 정식 운영 단계의 날짜가 아님.',
    'GET /api/v1/matches': 'limit 기본 50/상한 100. season_id와 event_id 동시 사용 시 같은 값이어야 함. date는 한국 날짜, unscheduled=true와 함께 사용 불가. 미지정 필터는 전체 일치 경기.',
    'GET /api/v1/matches/{match_id}': 'TBD·시각 미정·시즌 종료·수집 지연일 때 배팅 차단. bettingOpen과 bettingClosedReason을 렌더링.',
    'GET /api/v1/matches/{match_id}/stake-ranking': '각 팀 최대 5명. 공개 회원/로그인/거래 ID 없음. displayName은 현재 null.',
    'POST /api/v1/auth/session': '새 Google OAuth 후 5분 이내, 본문 없음. 서버 create_dt+6h 고정. 같은 세션 재등록·토큰 갱신은 연장되지 않음.',
    'GET /api/v1/auth/me': '선택 시즌을 season_id로 명시. 다중 활성 비데모 시즌에서 미선택 currentSeasonId=null/currentPoints=0. availableSeasonIds 제공.',
    'POST /api/v1/auth/logout': '본문 없음, 204 응답 본문 없음. 현재 서버 세션을 영구 철회. 프론트에서도 개인 상태를 지우고 Supabase signOut을 연결.',
    'POST /api/v1/auth/cheer': '시즌 첫 배팅 전에 확정 참가 팀 선택. 팀 변경 불가. 같은 팀 재요청은 안전. 실제 회원 ID를 본문에 보내지 않음.',
    'GET /api/v1/auth/cheer': 'season_id 필수. 본인 계약 없으면 HTTP 200 + JSON null. 완료 시즌 이력 조회 가능.',
    'POST /api/v1/auth/bets': '한 회원당 경기 한 번, 실제 시즌 잔액 차감. 같은 키/같은 입력 재전송은 기존 거래 반환. 새 키라도 중복 배팅 거절. 성공 이후 기존 요청은 경기 마감 후에도 재확인 가능.',
    'GET /api/v1/auth/bets': '본인 이력만. 시즌 미지정 시 모든 본인 시즌 이력. currentPoints/cheer는 이력 조회에서 null, 별도 context/me로 조회.',
    'GET /api/v1/auth/matches/{match_id}/context': '개인 응답 no-store. eligible/reason과 bettingOpen은 서로 다름. myRank는 본인이 선택한 팀 순위, 환불 시 null.',
    'POST /api/v1/admin/seasons/{season_id}/refresh': '관리자 전용. 공급자 series가 운영 도구로 미리 연결돼 있어야 함. 정상 사용자 앱에서 자동 호출하지 않음. 요청 중 네트워크 수집에는 DB 잠금을 유지하지 않음.',
    'POST /api/v1/admin/seasons/{season_id}/activate': 'upcoming 시즌만 활성화. 해당 시즌 초기 지급을 한 번 생성, 다른 시즌 잔액 유지. active 재요청은 initializedMembers=0.',
    'POST /api/v1/admin/seasons/{season_id}/finish': '해당 시즌의 미완료 경기·정산·환불이 없을 때 종료. 다른 활성 시즌 유지.',
    'POST /api/v1/admin/matches/{match_id}/result': '결과 저장과 정산을 원자적으로 처리. 동일 결과 재처리 가능, 확정 결과 변경 거절. score_a/score_b 둘 다 제공하거나 모두 생략. 취소에 점수 불가.',
}


def runtime_checks(schema):
    """Emit dependency-free validators from the same response schema as the TS types."""
    def check(model, value='value'):
        if '$ref' in model:
            return 'valid' + model['$ref'].split('/')[-1] + '(' + value + ')'
        if 'const' in model:
            return value + ' === ' + json.dumps(model['const'])
        if 'enum' in model:
            return '(' + json.dumps(model['enum']) + ' as readonly unknown[]).includes(' + value + ')'
        if 'anyOf' in model or 'oneOf' in model:
            return '(' + ' || '.join(check(part, value) for part in model.get('anyOf', model.get('oneOf'))) + ')'
        kind = model.get('type')
        if kind == 'null':
            return value + ' === null'
        if kind == 'object':
            checks = ['isRecord(' + value + ')']
            required = set(model.get('required', []))
            for key, part in model.get('properties', {}).items():
                quoted = json.dumps(key)
                condition = check(part, value + '[' + quoted + ']')
                if key not in required:
                    condition = '(!(' + quoted + ' in ' + value + ') || (' + condition + '))'
                checks.append(condition)
            return '(' + ' && '.join(checks) + ')'
        if kind == 'array':
            checks = ['Array.isArray(' + value + ')']
            if 'prefixItems' in model:
                checks.append(value + '.length === ' + str(len(model['prefixItems'])))
                checks.extend(check(part, value + '[' + str(index) + ']')
                              for index, part in enumerate(model['prefixItems']))
            elif isinstance(model.get('items'), dict):
                checks.append(value + '.every((item: unknown) => ' + check(model['items'], 'item') + ')')
            return '(' + ' && '.join(checks) + ')'
        if kind in {'integer', 'number'}:
            guard = 'Number.isSafeInteger' if kind == 'integer' else 'Number.isFinite'
            checks = ['typeof ' + value + " === 'number'", guard + '(' + value + ')']
            for field, op in [('minimum', '>='), ('maximum', '<='), ('exclusiveMinimum', '>'), ('exclusiveMaximum', '<')]:
                if field in model:
                    checks.append(value + ' ' + op + ' ' + str(model[field]))
            return '(' + ' && '.join(checks) + ')'
        if kind in {'string', 'boolean'}:
            checks = ['typeof ' + value + " === '" + kind + "'"]
            if kind == 'string' and model.get('format') == 'date-time':
                checks.append('Number.isFinite(Date.parse(' + value + '))')
            if kind == 'string' and model.get('format') == 'uuid':
                checks.append('/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(' + value + ')')
            return '(' + ' && '.join(checks) + ')'
        raise ValueError('Unsupported response schema: ' + repr(model))

    code = '\nfunction isRecord(value: unknown): value is Record<string, unknown> {\n'
    code += "  return value !== null && typeof value === 'object' && !Array.isArray(value);\n}\n"
    for name, model in sorted(schema['components']['schemas'].items()):
        code += '\nfunction valid' + name + '(value: unknown): value is ' + name + ' {\n  return ' + check(model) + ';\n}\n'
    code += '\nconst responseContracts = [\n'
    for path, methods in schema['paths'].items():
        for method, operation in methods.items():
            for status, response in operation['responses'].items():
                if not status.startswith('2'):
                    continue
                model = response.get('content', {}).get('application/json', {}).get('schema')
                expression = check(model) if model else 'value === null'
                code += '  { method: ' + json.dumps(method.upper()) + ', path: ' + json.dumps(path.removeprefix('/api/v1'))
                code += ', status: ' + status + ', validate: (value: unknown) => ' + expression + ' },\n'
    code += '''];
function validSuccessResponse(method: string, path: string, status: number, value: unknown): boolean {
  const parts = path.split('?')[0].split('/');
  const contract = responseContracts.find(row => {
    const expected = row.path.split('/');
    return row.method === method && expected.length === parts.length &&
      expected.every((part, index) => part.startsWith('{') || part === parts[index]);
  });
  return !!contract && contract.status === status && contract.validate(value);
}
'''
    return code


def generate(examples_path):
    # Source generation must never read the operator's .env or initialize DB/network.
    from app.core.config import Settings, get_settings
    Settings.model_config['env_file'] = None
    get_settings.cache_clear()
    from app.main import create_app
    schema = create_app().openapi()
    examples = json.loads(Path(examples_path).read_text(encoding='utf-8'))
    types = '// Generated from LoLPICK OpenAPI v' + schema['info']['version'] + '. Do not hand-edit schema types.\n'
    for name, model in sorted(schema['components']['schemas'].items()):
        types += '\nexport type ' + name + ' = ' + ts_type(model) + ';\n'
    types += runtime_checks(schema)
    types += (ROOT/'scripts/templates/frontend-client.ts.txt').read_text(encoding='utf-8')
    esc = html.escape
    cards = []
    operations = set()
    for path, methods in schema['paths'].items():
        for method, operation in methods.items():
            if method not in {'get','post'}:
                continue
            key = method.upper() + ' ' + path
            operations.add(key)
            group = 'admin' if '/admin/' in path else 'private' if '/auth/' in path else 'public'
            access = {'public':'익명 가능', 'private':'Google Bearer + 서버 세션', 'admin':'관리자 + 서버 세션 + 운영 MFA'}[group]
            example = examples[key]
            params = operation.get('parameters', [])
            rows = []
            for param in params:
                definition = param['schema']
                constraints = ', '.join(str(k)+'='+str(definition[k]) for k in
                    ('minimum','maximum','minLength','maxLength','default','format','enum') if k in definition)
                rows.append('<tr><td><code>'+esc(param['name'])+'</code></td><td>'+esc(param['in'])+'</td><td>'+
                    ('필수' if param.get('required') else '선택')+'</td><td><code>'+esc(ts_type(definition))+'</code><br>'+esc(constraints)+'</td></tr>')
            table = ('<div class="table-wrap"><table><thead><tr><th>이름</th><th>위치</th><th>필수 여부</th><th>형식 / 범위</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>') if rows else '<p class="muted">경로·쿼리·추가 헤더 입력 없음.</p>'
            body = operation.get('requestBody',{}).get('content',{}).get('application/json',{}).get('schema')
            body_html = '<p>본문 없음. Content-Type/빈 JSON 객체를 추가할 필요가 없습니다.</p>' if body is None else '<p>JSON body: <code>'+esc(ts_type(body))+'</code></p><pre>'+esc(json.dumps(example['body'],ensure_ascii=False,indent=2))+'</pre>'
            curl = 'curl -X '+method.upper()+' "http://127.0.0.1:8000/api/v1'+example['path']+'"'
            if group != 'public':
                curl += ' \\\n  -H "Authorization: Bearer <ACCESS_TOKEN>"'
            if any(p['name']=='Idempotency-Key' and p.get('required') for p in params):
                curl += ' \\\n  -H "Idempotency-Key: 00000000-0000-4000-8000-000000000001"'
            if example['body'] is not None:
                curl += ' \\\n  -H "Content-Type: application/json" \\\n  --data \''+json.dumps(example['body'],ensure_ascii=False,separators=(',',':'))+'\''
            responses = operation['responses']
            success = next((value.get('content',{}).get('application/json',{}).get('schema') for status,value in responses.items() if status.startswith('2')),None)
            success_html = '응답 본문 없음 (204)' if success is None else esc(ts_type(success))
            cards.append('<article class="route-card" data-group="'+group+'"><div class="route-title"><span class="method '+method+'">'+method.upper()+'</span>'+esc(path)+'</div><span class="badge">'+access+'</span><p>'+esc(NOTES[key])+'</p>'+table+body_html+
                '<details><summary>실제 요청 예제</summary><pre>'+esc(curl)+'</pre><p class="muted">Windows PowerShell에서는 curl.exe를 사용하거나 frontend-api.ts로 호출합니다. 예제 ID를 실제 ID로 교체합니다. 예제 UUID는 설명용이며 실제 새 작업마다 생성합니다.</p></details>'+
                '<details><summary>응답 계약 · 가상 HTTP 예제</summary><p>성공 타입: <code>'+success_html+'</code></p><p>예제 HTTP '+str(example['status'])+'</p><pre>'+esc(json.dumps(example['response'],ensure_ascii=False,indent=2) if example['response'] is not None else '(빈 본문)')+'</pre><p class="muted">공통 오류 타입 ErrorOut. 전체 필드 정의는 OpenAPI/TypeScript에 포함.</p></details></article>')
    assert operations == set(examples) == set(NOTES), 'Update handoff notes/examples for every operation.'
    template=(ROOT/'scripts/templates/api-guide.html').read_text(encoding='utf-8')
    font = base64.b64encode((ROOT/'scripts/templates/guide-font.woff2').read_bytes()).decode()
    template=template.replace('@@FONT@@',font)
    license_text=(ROOT/'scripts/templates/OFL-font.txt').read_text(encoding='utf-8')
    template=template.replace('</head>', '<!-- Embedded subset font license:\n'+license_text.replace('--','- -')+'\n--></head>')
    template=template.replace('@@CARDS@@',''.join(cards)).replace('@@SCHEMA@@',json.dumps(schema,ensure_ascii=False).replace('<','\\u003c'))
    template=template.replace('@@CLIENT@@',json.dumps(types,ensure_ascii=False).replace('<','\\u003c'))
    (ROOT/'docs/API_GUIDE.html').write_text(template,encoding='utf-8')
    (ROOT/'docs/openapi.json').write_text(json.dumps(schema,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'docs/frontend-api.ts').write_text(types,encoding='utf-8')
    print('Generated API guide, OpenAPI and TypeScript for',len(operations),'operations.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--examples',type=Path,required=True)
    generate(parser.parse_args().examples)
