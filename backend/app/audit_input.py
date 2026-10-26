"""Safe diagnostics: numeric fields, fixed enums and DB-confirmed identifiers."""
import json
import re
from app.db.domain import CODES


def audit_route(scope):
    path = scope.get('path', '')
    for route in CODES['route'].values():
        pattern = re.sub(r'\{[^}]+\}', '[^/]{1,80}', re.escape(route).replace(r'\{', '{').replace(r'\}', '}'))
        if re.fullmatch(pattern, path):
            return route
    return '[unmatched]'


def captured_input(data, complete, state):
    from app.telemetry import captured_json
    capture = captured_json(data, complete)
    if not complete or not data:
        return capture
    try:
        body = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        return capture
    if not isinstance(body, dict):
        return capture
    safe = dict(state.get('audit_safe_body', {}))
    for key in ('amount', 'score_a', 'score_b'):
        value = body.get(key)
        if type(value) is int and abs(value) <= 2**53 - 1:
            safe[key] = value
    if body.get('result') in ('team_a_win', 'team_b_win', 'draw', 'cancelled'):
        safe['result'] = body['result']
    if isinstance(capture, dict):
        capture['safe_values'] = safe
    return capture


def response_error_code(data, server_error):
    code = server_error.get('code') if isinstance(server_error, dict) else None
    try:
        response = json.loads(data)
        detail = response.get('detail') if isinstance(response, dict) else None
        if isinstance(detail, dict):
            code = detail.get('code') or code
    except (ValueError, UnicodeError, RecursionError):
        pass
    return code if isinstance(code, str) and re.fullmatch('[a-z][a-z0-9_]{0,79}', code) else None
