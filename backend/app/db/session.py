from functools import lru_cache

from time import monotonic
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import get_settings, DatabaseConfigurationError


class TransactionDeadlineExceeded(SQLAlchemyError):
    code = "transaction_deadline_exceeded"


def install_transaction_budget(engine, seconds):
    @event.listens_for(engine, "begin")
    def started(connection):
        connection.info["lolpick_transaction_started"] = monotonic()

    def verify(connection):
        began = connection.info.get("lolpick_transaction_started")
        if began is not None and monotonic() - began >= seconds:
            raise TransactionDeadlineExceeded("Database transaction deadline exceeded.")

    @event.listens_for(engine, "before_cursor_execute")
    def before_statement(connection, cursor, statement, parameters, context, executemany):
        verify(connection)

    @event.listens_for(engine, "commit")
    def before_commit(connection):
        verify(connection)  # Refuse commit BEFORE issuing DBAPI commit.
        connection.info.pop("lolpick_transaction_started", None)

    @event.listens_for(engine, "rollback")
    def rolled_back(connection):
        connection.info.pop("lolpick_transaction_started", None)

    return engine


def connection_options(settings, *, audit=False):
    return {"application_name": "lolpick-audit" if audit else "lolpick-backend", "prepare_threshold": None,
            "options": (f"-c statement_timeout={min(settings.db_statement_timeout_ms, 5000) if audit else settings.db_statement_timeout_ms} "
                        f"-c lock_timeout={min(settings.db_lock_timeout_ms, 1000) if audit else settings.db_lock_timeout_ms} "
                        f"-c idle_in_transaction_session_timeout={settings.db_idle_transaction_timeout_ms}")}


def verified_engine(engine, settings):
    if settings.app_env != "production":
        return engine
    from app.db.readiness import verify_runtime, DatabaseReadinessError
    try:
        with Session(engine) as db:
            verify_runtime(db, require_restricted_role=True)
    except (SQLAlchemyError, DatabaseReadinessError):
        engine.dispose()
        raise DatabaseConfigurationError("Runtime schema or restricted database role is not ready.") from None
    return engine


@lru_cache
def get_engine():
    settings = get_settings()
    return verified_engine(install_transaction_budget(create_engine(
        settings.connection_url(),
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=5,
        pool_recycle=1800,
        hide_parameters=True,
        connect_args=connection_options(settings),
    ), settings.db_transaction_budget_seconds), settings)


def get_db():
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    with factory() as session:
        yield session


def dispose_engine():
    if get_engine.cache_info().currsize:
        get_engine().dispose()
        get_engine.cache_clear()


@lru_cache
def get_audit_engine():
    settings = get_settings()
    return verified_engine(install_transaction_budget(create_engine(settings.connection_url(), pool_pre_ping=True, pool_size=1, max_overflow=0,
                         pool_timeout=1, pool_recycle=1800, hide_parameters=True,
                         connect_args=connection_options(settings, audit=True)), min(10, settings.db_transaction_budget_seconds)), settings)


def dispose_audit_engine():
    if get_audit_engine.cache_info().currsize:
        get_audit_engine().dispose()
        get_audit_engine.cache_clear()
