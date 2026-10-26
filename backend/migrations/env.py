from logging.config import fileConfig
from alembic import context
from sqlalchemy import create_engine, pool, text
from app.core.config import get_settings
from app.db.ddl import render
from app.db.schema import REVISION
from app.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
settings = get_settings()


def include_name(name, type_, parent_names):
    return name == settings.db_schema if type_ == "schema" else True


def configure(**kwargs):
    context.configure(target_metadata=Base.metadata, include_schemas=True,
                      include_name=include_name, version_table_schema=settings.db_schema,
                      version_table="lolpick_alembic_version", compare_type=True, **kwargs)


def run_migrations_offline():
    start = context.get_starting_revision_argument()
    target = context.get_revision_argument()
    if start not in (None, "0008_native_session_lookup", "0009_user_actions", REVISION) or target not in ("head", REVISION, "base"):
        raise RuntimeError("Earlier database revisions require the separate maintenance archive.")
    configure(url=settings.connection_url(migration=True), literal_binds=True,
              dialect_opts={"paramstyle": "named"})
    if start is None and target in ("head", REVISION):
        context.execute(render())
    else:
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_online():
    engine = create_engine(settings.connection_url(migration=True), poolclass=pool.NullPool,
                           hide_parameters=True, connect_args={"prepare_threshold": None})
    try:
        with engine.connect() as connection, connection.begin():
            if connection.scalar(text("SELECT to_regclass('lolpick.alembic_version')")) is not None:
                raise RuntimeError("Earlier database revisions require the separate maintenance archive.")
            marker_exists = connection.scalar(text("SELECT to_regclass('lolpick.lolpick_alembic_version')")) is not None
            if marker_exists:
                rows = connection.scalars(text("SELECT version_num FROM lolpick.lolpick_alembic_version")).all()
                if rows not in (["0008_native_session_lookup"], ["0009_user_actions"], [REVISION]):
                    raise RuntimeError("Earlier database revisions require the separate maintenance archive.")
            else:
                connection.execute(text("CREATE SCHEMA IF NOT EXISTS lolpick"))
            configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
