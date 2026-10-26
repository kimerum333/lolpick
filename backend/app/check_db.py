"""Read-only connection/schema check; never prints the connection string."""
from sqlalchemy import text

from app.db.session import get_engine
from app.db.schema import TABLE_SPECS
from app.db.readiness import verify_runtime


def main():
    engine = None
    try:
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            print("Database connection: OK")
            exists = connection.scalar(text("SELECT to_regclass('lolpick.lolpick_alembic_version')"))
            if exists is None:
                print("Schema is not initialized. Use maintenance/backend to run migrations with the owner connection.")
                raise SystemExit(2)
            revision = verify_runtime(connection, require_restricted_role=True)
            print("Migration revision:", revision)
            print("Backend role and native-session permissions: OK")
            for spec in TABLE_SPECS:
                name = spec["name"]
                count = connection.scalar(text(f"SELECT count(*) FROM lolpick.lolpick_{name}"))
                print(f"{name}: {count} rows")
    except Exception as exc:
        print(f"Database check failed ({type(exc).__name__}).")
        print("Check DATABASE_URL, database password, SSL, network access, and Session pooler port 5432.")
        raise SystemExit(1) from None
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
