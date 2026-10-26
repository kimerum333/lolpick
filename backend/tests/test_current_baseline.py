"""Current initialization and already-installed head without legacy modules."""
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import BACKEND_ROOT, get_settings
from app.db.ddl import render
from app.db.schema import REVISION


@pytest.fixture
def offline_config(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://operator:unused@localhost/postgres")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql+psycopg://operator:unused@localhost/postgres")
    get_settings.cache_clear()
    stream = StringIO()
    config = Config(str(BACKEND_ROOT / "alembic.ini"), output_buffer=stream)
    yield config, stream
    get_settings.cache_clear()


def test_current_head_has_no_historical_schema_dependencies():
    scripts = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini")))
    revisions = list(scripts.walk_revisions())
    assert [revision.revision for revision in revisions] == [REVISION, "0009_user_actions", "0008_native_session_lookup"]
    assert revisions[0].down_revision == "0009_user_actions"
    assert revisions[1].down_revision == "0008_native_session_lookup"
    assert revisions[2].down_revision is None
    assert not list((Path(BACKEND_ROOT) / "app/db").glob("schema_v*.py"))


def test_fresh_offline_initialization_is_the_current_schema(offline_config):
    config, stream = offline_config
    command.upgrade(config, "head", sql=True)
    sql = stream.getvalue()
    assert render().strip() in sql
    assert "read_native_session" in sql and REVISION in sql
    assert "DROP TABLE" not in sql and "CREATE TABLE lolpick.lolpick_bets" not in sql


def test_installed_0008_does_not_replay_baseline_ddl(offline_config):
    config, stream = offline_config
    command.upgrade(config, "0008_native_session_lookup:head", sql=True)
    sql = stream.getvalue()
    assert "route_code BETWEEN 0 AND 16" in sql
    assert "CREATE TABLE" not in sql and "DROP TABLE" not in sql
    assert "guard_member" not in sql and "post_point_entry" in sql
    assert "route_code BETWEEN 0 AND 19" in sql and "archive_file" in sql


def test_installed_head_has_no_schema_changes(offline_config):
    config, stream = offline_config
    command.upgrade(config, f"{REVISION}:head", sql=True)
    assert not any(word in stream.getvalue() for word in ("CREATE", "ALTER", "INSERT", "DROP"))


def test_earlier_revision_fails_without_conversion_sql(offline_config):
    config, stream = offline_config
    with pytest.raises(RuntimeError, match="separate maintenance archive"):
        command.upgrade(config, "0007_unified_ledger:head", sql=True)
    assert stream.getvalue() == ""
