"""Handoff schema must match the application rather than stale generated files."""
import json
from pathlib import Path
from app.main import create_app


def test_saved_schema_and_embedded_guide_match_app():
    import re
    root = Path(__file__).resolve().parents[1]
    schema = create_app().openapi()
    saved = json.loads((root/'docs/openapi.json').read_text(encoding='utf-8'))
    assert saved == schema
    guide = (root/'docs/API_GUIDE.html').read_text(encoding='utf-8')
    match = re.search(r'<script id="openapi" type="application/json">(.*?)</script>',guide,re.S)
    assert json.loads(match.group(1)) == schema
    client = re.search(r'<script id="typescript" type="application/json">(.*?)</script>',guide,re.S)
    assert json.loads(client.group(1)) == (root/'docs/frontend-api.ts').read_text(encoding='utf-8')
    assert guide.count('class="route-card"') == 21
    assert '@@SCHEMA@@' not in guide and '@@CARDS@@' not in guide
