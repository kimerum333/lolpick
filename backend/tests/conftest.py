import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings

# Block real .env loading before app.main creates its module-level app.
Settings.model_config["env_file"] = None
get_settings.cache_clear()
from app.db.session import get_db
from app.main import create_app
from app.models import Base

# Never read the real backend/.env while running verification on the user's tree.
# Dedicated integration URLs are supplied explicitly by the disposable DB fixture.


@pytest.fixture(autouse=True)
def private_audit(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DIRECTORY", str(tmp_path / "logs"))
    monkeypatch.setenv("SYSTEM_LOG_ENABLED", "false")
    monkeypatch.setenv("RATE_PUBLIC_PER_MINUTE", "10000")
    monkeypatch.setenv("RATE_AUTH_PER_MINUTE", "1000")
    monkeypatch.setenv("RATE_ADMIN_PER_MINUTE", "1000")
    get_settings.cache_clear()
    yield
    import logging
    from app.audit_journal import _journal

    for handler in list(logging.getLogger("lolpick.http_audit").handlers):
        handler.close()
        logging.getLogger("lolpick.http_audit").removeHandler(handler)
    _journal.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite+pysqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": {"lolpick": None}},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("SYSTEM_LOG_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()
