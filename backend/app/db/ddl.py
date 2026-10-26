"""Current empty-database initializer. Importing does not contact a database."""
from pathlib import Path
import re
from app.db.schema import REVISION, SCHEMA, validate_schema_name


def _source(schema):
    validate_schema_name(schema)
    value = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    return re.sub(r"\blolpick\b", schema, value)


def install_sql(schema=SCHEMA):
    source = _source(schema)
    marker = f"CREATE SCHEMA {schema};"
    if source.count(marker) != 1 or not source.rstrip().endswith("COMMIT;"):
        raise RuntimeError("Malformed current DDL payload")
    return source.split(marker, 1)[1].rsplit("COMMIT;", 1)[0]


def native_session_sql(schema=SCHEMA):
    validate_schema_name(schema)
    source = Path(__file__).with_name("session_lookup.sql").read_text(encoding="utf-8")
    return re.sub(r"\blolpick\b", schema, source)


def render(schema=SCHEMA):
    source = _source(schema)
    version = (
        f"CREATE TABLE {schema}.lolpick_alembic_version (version_num varchar(32) PRIMARY KEY);\n"
        f"INSERT INTO {schema}.lolpick_alembic_version VALUES ('{REVISION}');\n"
        f"ALTER TABLE {schema}.lolpick_alembic_version ENABLE ROW LEVEL SECURITY;\n"
    )
    return source.rsplit("COMMIT;", 1)[0] + native_session_sql(schema) + "\n" + version + "COMMIT;\n"
