"""Provider identities and current display fields share one team row."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.models import ApiSource, Team
from app.db.compact import team_display, checked_document
from app.services.sessions import utc


def preserve_source(db, code):
    if not isinstance(code, str) or not code.strip() or len(code) > 80:
        raise ValueError("Invalid provider code.")
    # PostgreSQL consumes identity values even when ON CONFLICT inserts nothing.
    # Repeated hourly imports must not exhaust the small provider dictionary.
    existing = db.scalar(select(ApiSource).where(ApiSource.code == code))
    if existing is not None:
        return existing
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    db.execute(insert(ApiSource.__table__).values(code=code).on_conflict_do_nothing(index_elements=["code"]))
    return db.scalar(select(ApiSource).where(ApiSource.code == code))


def preserve_team(db, source, data, seen_at):
    external = str(data["id"])
    if not external.strip() or len(external) > 160:
        raise ValueError("Invalid provider team identity.")
    provider = preserve_source(db, source)
    seen_at = utc(seen_at)
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    display = team_display(data, external)
    fields = {
        "name": display["name"], "acronym": display.get("acronym"),
        "logo_url": display.get("logo"),
        "display_data": checked_document(
            {key: value for key, value in display.items() if key in {"region", "color", "location"}},
            {"region", "color", "location"}, 1024,
        ),
        "last_seen_at": seen_at,
    }
    # Missing provider fields must not erase existing manually curated display.
    fields["display_data"] = fields["display_data"] or {}
    for key in ("region", "color", "location"):
        if key not in data or data[key] is None:
            fields["display_data"].pop(key, None)
    for key, cap in (("name", 255), ("acronym", 80), ("logo_url", 2048)):
        if fields[key] is not None and len(fields[key]) > cap:
            raise ValueError("Team display field exceeds the schema limit.")
    statement = insert(Team.__table__).values(
        api_source_id=provider.id, external_team_id=external, **fields
    )
    updates = dict(fields)
    # Null/omitted/blank provider values mean "unavailable", not an instruction
    # to delete a curated field. Clearing display data needs an explicit admin edit.
    for column, key in (("name", "name"), ("acronym", "acronym"), ("logo_url", "logo")):
        if not data.get(key):
            updates.pop(column)
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy import func
        updates["display_data"] = func.coalesce(cast(Team.display_data, JSONB), cast({}, JSONB)).op("||")(cast(statement.excluded.display_data, JSONB))
    else:
        from sqlalchemy import func
        updates["display_data"] = func.json_patch(func.coalesce(Team.display_data, '{}'), statement.excluded.display_data)
    db.execute(statement.on_conflict_do_update(
        index_elements=["api_source_id", "external_team_id"],
        set_=updates, where=Team.last_seen_at <= seen_at,
    ))
    return db.scalar(select(Team).where(
        Team.api_source_id == provider.id, Team.external_team_id == external
    ).execution_options(populate_existing=True))
