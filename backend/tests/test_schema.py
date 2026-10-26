"""Current schema and runtime conversion helpers; no historical fixtures."""
from hashlib import sha256
from datetime import datetime, timezone
import pytest
import sqlalchemy as sa
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects.postgresql import dialect
from app.db.schema import build_metadata
from app.db.ddl import render
from app.db.compact import (key_hash, encode_issues, decode_issues, team_display,
                           compact_match_document, match_state, selected_side)

def test_postgres_ddl_builds_and_all_relations_resolve():
    meta = build_metadata()
    assert len(meta.tables) == 9
    for table in meta.sorted_tables:
        assert str(CreateTable(table).compile(dialect=dialect()))
        for fk in table.foreign_keys:
            assert fk.column is not None
    assert "DROP " not in render()
    assert "CREATE SCHEMA lolpick;" in render()

def test_internal_keys_are_numeric_and_auth_is_separate():
    for table in build_metadata().tables.values():
        for col in table.primary_key:
            assert isinstance(col.type, (sa.BigInteger, sa.SmallInteger))
    members = build_metadata().tables["lolpick.lolpick_members"]
    assert isinstance(members.c.auth_user_id.type, sa.Uuid)
    assert members.c.id.identity.always

def test_schema_name_injection_rejected():
    with pytest.raises(ValueError):
        render("lolpick; DROP SCHEMA public CASCADE")

def test_hash_preserves_exact_original_key():
    assert key_hash("season:old-id:member:uuid:open") == sha256(b"season:old-id:member:uuid:open").digest()
    assert len(key_hash("x")) == 32
    assert key_hash("X") != key_hash("x")
    assert key_hash(" x") != key_hash("x")

def test_issue_bitmask_roundtrip_and_unknown_rejected():
    issues = ["draw", "settlement_blocked", "draw"]
    assert decode_issues(encode_issues(issues)) == ["draw", "settlement_blocked"]
    with pytest.raises(ValueError):
        encode_issues(["new_unmapped_provider_error"])
    with pytest.raises(ValueError):
        decode_issues(4096)

def test_team_display_retains_historical_values_without_repeated_identity():
    out = team_display({"id": "10", "name": "OLD NAME", "color": "#64748b", "region": "", "logo": None}, "10")
    assert out == {"name": "OLD NAME"}
    with pytest.raises(ValueError):
        team_display({"id": "11", "name": "OTHER"}, "10")

def test_match_compaction_retains_draw_and_forfeit():
    out, flags, status, forfeit = compact_match_document(
        {
            "normalized": {"name": "Final", "draw": True, "forfeit": True, "source_status": "finished", "issues": []},
            "raw": {"ignored": "source archive"},
        }
    )
    assert out == {"name": "Final"}
    assert decode_issues(flags) == ["draw"]
    assert status == 4 and forfeit

def test_no_invented_historical_settlement_totals():
    now = datetime.now(timezone.utc)
    state = match_state(
        {
            "status": "settled",
            "result": "team_a_win",
            "winner_team_id": "A",
            "team_a_id": "A",
            "team_b_id": "B",
            "result_recorded_at": now,
            "settlement_status": "settled",
            "settled_at": now,
        }
    )
    assert state["status"] == 3 and state["settlement_status"] == 2
    assert state["settlement_pool"] is None and state["settlement_paid"] is None

def test_selected_side_never_accepts_nonparticipant():
    assert selected_side("B", "A", "B") == 2
    with pytest.raises(ValueError):
        selected_side("C", "A", "B")
