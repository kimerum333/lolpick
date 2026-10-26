from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class DatabaseConfigurationError(ValueError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    system_log_enabled: bool = True
    log_directory: Path = BACKEND_ROOT / "logs"
    audit_max_events: int = Field(default=200000, ge=100, le=2000000)
    audit_max_bytes: int = Field(default=256 * 1024 * 1024, ge=1024 * 1024, le=4 * 1024 * 1024 * 1024)
    audit_retention_days: int = Field(default=30, ge=1, le=365)
    audit_file_segment_bytes: int = Field(default=50 * 1024 * 1024, ge=32768, le=1024 * 1024 * 1024)
    request_body_timeout: float = Field(default=5, ge=0.05, le=30)
    request_max_concurrent: int = Field(default=32, ge=1, le=256)
    rate_public_per_minute: int = Field(default=120, ge=1, le=10000)
    rate_auth_per_minute: int = Field(default=20, ge=1, le=1000)
    rate_admin_per_minute: int = Field(default=30, ge=1, le=1000)
    # IP category caps defend the server; identity caps isolate shared-IP users.
    rate_auth_ip_per_minute: int = Field(default=120, ge=1, le=10000)
    rate_admin_ip_per_minute: int = Field(default=120, ge=1, le=10000)
    rate_member_per_minute: int = Field(default=120, ge=1, le=10000)
    rate_session_per_minute: int = Field(default=90, ge=1, le=10000)
    admin_require_mfa: bool = True
    trusted_proxy_cidrs: list[str] = []
    db_transaction_budget_seconds: int = Field(default=30, ge=1, le=300)
    db_statement_timeout_ms: int = Field(default=15000, ge=100, le=120000)
    db_lock_timeout_ms: int = Field(default=3000, ge=100, le=30000)
    db_idle_transaction_timeout_ms: int = Field(default=10000, ge=1000, le=120000)
    database_url: SecretStr | None = None
    migration_database_url: SecretStr | None = None
    db_schema: str = "lolpick"
    db_sslmode: str | None = None
    db_sslrootcert: str | None = None
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=0, ge=0, le=20)
    db_connect_timeout: int = Field(default=5, ge=1, le=30)
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    supabase_url: str | None = None
    supabase_publishable_key: SecretStr | None = None
    supabase_auth_timeout: float = Field(default=5, ge=1, le=30)
    pandascore_token: SecretStr | None = None
    pandascore_timeout: float = Field(default=10, ge=1, le=30)
    pandascore_collection_budget_seconds: int = Field(default=60, ge=10, le=300)
    pandascore_page_size: int = Field(default=100, ge=1, le=100)
    pandascore_max_pages: int = Field(default=20, ge=1, le=100)
    pandascore_retries: int = Field(default=2, ge=0, le=3)
    pandascore_max_retry_delay: float = Field(default=5, ge=0, le=10)
    pandascore_max_staleness_seconds: int = Field(default=4500, ge=60, le=86400)
    public_snapshot_max_age_seconds: int = Field(default=86400, ge=60, le=86400)
    public_circuit_seconds: int = Field(default=5, ge=1, le=30)

    @model_validator(mode="after")
    def production_audit_required(self):
        if self.app_env == "production" and not self.system_log_enabled:
            raise ValueError("Production requires SYSTEM_LOG_ENABLED=true.")
        if self.app_env == 'production' and not self.admin_require_mfa:
            raise ValueError('Production requires ADMIN_REQUIRE_MFA=true.')
        return self

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def valid_proxy_networks(cls, value):
        import ipaddress
        for network in value:
            ipaddress.ip_network(network, strict=True)
        return value

    def auth_configuration(self) -> tuple[str, str]:
        from app.core.auth import AuthError
        raw = (self.supabase_url or "").rstrip("/")
        key = self.supabase_publishable_key.get_secret_value() if self.supabase_publishable_key else ""
        try:
            parts = urlsplit(raw)
            valid = (parts.scheme == "https" and parts.hostname and not parts.username
                     and not parts.password and parts.path == "" and not parts.query
                     and not parts.fragment and parts.port in (None, 443))
        except ValueError:
            valid = False
        if not valid or not key.startswith("sb_publishable_") or len(key) <= len("sb_publishable_"):
            raise AuthError(503, "auth_not_configured", "Configure SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY.")
        return raw, key

    @field_validator("db_schema")
    @classmethod
    def fixed_schema(cls, value: str) -> str:
        if value != "lolpick":
            raise ValueError("This release uses the fixed PostgreSQL schema 'lolpick'.")
        return value

    @field_validator("app_env")
    @classmethod
    def valid_environment(cls, value: str) -> str:
        if value not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test, or production.")
        return value

    @field_validator("db_sslmode")
    @classmethod
    def valid_sslmode(cls, value: str) -> str:
        if value is not None and value not in {"require", "verify-ca", "verify-full", "disable"}:
            raise ValueError("Unsupported DB_SSLMODE.")
        return value

    def connection_url(self, *, migration: bool = False) -> URL:
        secret = (self.migration_database_url or self.database_url) if migration else self.database_url
        if secret is None or not secret.get_secret_value():
            raise DatabaseConfigurationError("DATABASE_URL is not configured.")
        try:
            url = make_url(secret.get_secret_value())
        except Exception:
            raise DatabaseConfigurationError("DATABASE_URL has an invalid format.") from None
        if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
            raise DatabaseConfigurationError("Only PostgreSQL with psycopg is supported.")
        if not url.host or not url.database or not url.username:
            raise DatabaseConfigurationError("DATABASE_URL requires host, database, and username.")
        if url.port == 6543:
            raise DatabaseConfigurationError("Use a direct connection or Session pooler on port 5432, not Transaction pooler.")
        strength = {'disable':0, 'require':1, 'verify-ca':2, 'verify-full':3}
        url_mode = url.query.get('sslmode')
        if url_mode is not None and url_mode not in strength:
            raise DatabaseConfigurationError('Unsupported URL sslmode.')
        candidates = [mode for mode in (self.db_sslmode, url_mode) if mode is not None]
        mode = max(candidates, key=strength.get) if candidates else (
            'verify-full' if self.app_env == 'production' else 'require')
        if self.app_env == 'production' and mode != 'verify-full':
            raise DatabaseConfigurationError('Production requires SSL certificate and hostname verification (verify-full).')
        if not migration and self.app_env == "production" and url.username.split(".")[0] in {
                "postgres", "supabase_admin", "supabase_auth_admin", "service_role", "anon", "authenticated"}:
            raise DatabaseConfigurationError("Production requires a dedicated restricted database login.")
        # URL objects avoid Alembic ConfigParser interpolation of percent-encoded passwords.
        options = {'sslmode':mode, 'connect_timeout':str(self.db_connect_timeout)}
        if self.db_sslrootcert:
            options['sslrootcert'] = self.db_sslrootcert
        return url.set(drivername="postgresql+psycopg").update_query_dict(options)


@lru_cache
def get_settings() -> Settings:
    return Settings()
