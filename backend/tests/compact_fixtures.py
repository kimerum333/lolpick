"""Explicit migration of legacy test fixtures to the v7 numeric schema.

Production models never accept string/UUID internal keys. These builders resolve
the test's public identifiers before constructing real v7 models.
"""

from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from app.models import Season, Match, Member, MemberSession, PointTransaction, Team
from app.services.teams import preserve_team, preserve_source
from app.db.compact import key_hash, compact_match_document, label_for


def internal(db, model, value):
    if value is None or type(value) is int:
        return value
    if model in (Season, Match) and isinstance(value, str):
        attribute = model.public_code
    elif model is Member and isinstance(value, UUID):
        attribute = model.auth_user_id
    elif model is MemberSession and isinstance(value, UUID):
        attribute = model.auth_session_id
    elif model is Team and isinstance(value, str):
        attribute = model.external_team_id
    else:
        return value  # Preserve malformed input for the validation under test.
    with db.no_autoflush:
        for row in db.new:
            if isinstance(row, model) and getattr(row, attribute.key) == value:
                db.flush()
                return row.id
        result = db.scalar(select(model.id).where(attribute == value))
    return result if result is not None else value


def lookup(db, model, value):
    return db.get(model, internal(db, model, value))


def make_match(db, **values):
    values["public_code"] = values.pop("id")
    values["season_id"] = internal(db, Season, values["season_id"])
    data = values.pop("api_data", {})
    now = values.get("api_synced_at") or datetime.now(timezone.utc)
    source = values.pop("api_source")
    snapshots = {str(row["id"]): row for row in data.get("teams", [])}
    for slot in ("a", "b"):
        external = values[f"team_{slot}_id"]
        if external is None:
            values[f"team_{slot}_id"] = None
            continue
        team = preserve_team(db, source, snapshots.get(external, {"id": external, "name": external}), now)
        values[f"team_{slot}_id"] = team.id
    values["api_source_id"] = preserve_source(db, source).id
    display, bits, provider, forfeit = compact_match_document(data)
    values.update(
        display_data=display,
        issue_flags=bits,
        provider_status=label_for("provider_status", provider) if provider else None,
        is_forfeit=forfeit,
    )
    values.pop("winner_team_id", None)
    settled, refunded = values.pop("settled_at", None), values.pop("refunded_at", None)
    values.pop("settlement_remainder", None)
    if values.get("status") == "settled":
        values["status"] = "finished"
        values.setdefault("settlement_status", "settled")
    values["settlement_at"] = settled or refunded
    return Match(**values)


def make_entry(db, **values):
    values.pop("id", None)
    for name, model in (("member_id", Member), ("season_id", Season), ("match_id", Match)):
        if name in values:
            values[name] = internal(db, model, values[name])
    values["idempotency_key_hash"] = key_hash(values.pop("idempotency_key"))
    return PointTransaction(**values)


def place_wager(db, *, member_id, season_id, match_id, selected_team_id, amount, idempotency_key):
    """Fund the wager through the real service; no mutable bet row exists."""
    from app.models import Wager
    from app.services.points import apply_points, PointOperationError
    match = lookup(db, Match, match_id)
    if match is None:
        raise PointOperationError("Match does not exist.")
    external_a = match.team_a.external_team_id if match.team_a else None
    external_b = match.team_b.external_team_id if match.team_b else None
    side = 1 if selected_team_id == external_a else 2 if selected_team_id == external_b else None
    entry = apply_points(db, member_id=internal(db, Member, member_id),
        season_id=internal(db, Season, season_id), match_id=match.id, selected_side=side,
        amount=-amount, reason="bet_stake", idempotency_key=idempotency_key)
    return db.get(Wager, entry.id)
