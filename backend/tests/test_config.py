import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.main import create_app


def test_percent_encoded_password_round_trips_without_logging_secret():
    settings = Settings(
        _env_file=None,
        database_url=SecretStr("postgresql://postgres.project:p%40ss%23%24%25word@host.example:5432/postgres"),
    )
    url = settings.connection_url()
    assert url.password == "p@ss#$%word"
    assert url.drivername == "postgresql+psycopg"
    assert url.query["sslmode"] == "require"
    assert "p@ss" not in repr(settings)


def test_transaction_pooler_is_rejected():
    settings = Settings(_env_file=None, database_url="postgresql://postgres:p@host:6543/postgres")
    with pytest.raises(ValueError, match="Session pooler"):
        settings.connection_url()


@pytest.mark.parametrize("username", ["postgres", "postgres.project", "supabase_admin", "service_role", "anon"])
def test_production_rejects_owner_and_browser_database_credentials(username):
    settings = Settings(_env_file=None, app_env="production", system_log_enabled=True,
                        database_url=f"postgresql://{username}:p@host:5432/postgres")
    with pytest.raises(ValueError, match="dedicated restricted"):
        settings.connection_url()
    assert settings.connection_url(migration=True).username == username


def test_production_cannot_disable_ssl():
    settings = Settings(
        _env_file=None,
        app_env="production",
        system_log_enabled=True,
        db_sslmode="disable",
        database_url="postgresql://postgres:p@host:5432/postgres",
    )
    with pytest.raises(ValueError, match="SSL"):
        settings.connection_url()


def test_liveness_works_without_database_and_readiness_does_not(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/health").status_code == 200
        response = client.get("/api/v1/ready")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "configuration_error"
    get_settings.cache_clear()


def test_write_endpoints_are_not_exposed(client):
    assert client.post("/api/v1/bets", json={"amount": 1000}).status_code == 404
    assert client.get("/api/v1/users").status_code == 404
