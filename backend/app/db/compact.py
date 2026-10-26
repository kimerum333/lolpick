"""Pure conversion helpers for the v7 design; no database or network calls.

The API boundary retains public identifiers; the database stores compact values.
Unrepresentable input fails instead of being silently truncated or misclassified.
"""

from __future__ import annotations

import hashlib
import json
from app.db.schema import CODES, ISSUES


def code_for(group: str, value: str) -> int:
    for code, label in CODES[group].items():
        if label == value:
            return code
    raise ValueError(f"Unmapped {group}; explicit migration is required.")


def label_for(group: str, value: int) -> str:
    if type(value) is not int:
        raise ValueError("Code must be an integer.")
    return CODES[group][value]


def key_hash(key: str, *, max_chars: int = 200) -> bytes:
    if not isinstance(key, str) or not 1 <= len(key) <= max_chars:
        raise ValueError("Invalid idempotency/event key length.")
    # Frozen encoding: exact original UTF-8 bytes. No lowercasing, trimming,
    # UUID reformatting or new-id substitution when migrating historical keys.
    return hashlib.sha256(key.encode("utf-8")).digest()


def encode_issues(values: list[str]) -> int:
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ValueError("Issues must be a list of known strings.")
    bits = 0
    for value in values:
        if value not in ISSUES:
            raise ValueError("Unmapped issue; preserve the source and stop migration.")
        bits |= ISSUES[value]
    return bits


def decode_issues(bits: int) -> list[str]:
    if type(bits) is not int or not 0 <= bits <= 4095:
        raise ValueError("Unknown issue bits.")
    return sorted(name for name, flag in ISSUES.items() if bits & flag)


def checked_document(data: dict, allowed: set[str], cap: int) -> dict | None:
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("Document contains unmapped fields.")
    # JSONB text uses spacing; match that conservative representation in checks.
    if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")) > cap:
        raise ValueError("Document is too large; archive the original and review it.")
    return data or None


def team_display(snapshot: dict, external_id: str) -> dict:
    allowed = {"id", "name", "acronym", "logo", "region", "color", "location"}
    if not isinstance(snapshot, dict) or set(snapshot) - allowed:
        raise ValueError("Unmapped team snapshot fields.")
    if "id" in snapshot and str(snapshot["id"]) != external_id:
        raise ValueError("Team snapshot identity differs from the provider identity.")
    output = {k: v for k, v in snapshot.items() if k != "id" and v is not None and v != ""}
    output.setdefault("name", external_id)
    if output.get("color") == "#64748b":
        output.pop("color")  # API default, not a historical override.
    if any(not isinstance(v, str) for v in output.values()):
        raise ValueError("Team display fields must be strings.")
    checked_document(output, allowed - {"id"}, 4096)
    return output


def compact_match_document(api_data: dict) -> tuple[dict | None, int, int | None, bool]:
    if not isinstance(api_data, dict):
        raise ValueError("Invalid match document.")
    normalized = api_data.get("normalized", {})
    if not isinstance(normalized, dict):
        raise ValueError("Invalid normalized match document.")
    allowed = {
        "name",
        "round",
        "league",
        "tournament",
        "match_type",
        "number_of_games",
        "source_status",
        "forfeit",
        "draw",
        "issues",
    }
    if set(normalized) - allowed:
        raise ValueError("Unmapped normalized fields; do not silently discard them.")
    display = {
        k: v
        for k, v in normalized.items()
        if k in {"name", "league", "tournament", "match_type", "number_of_games"} and v is not None and v != ""
    }
    bits = encode_issues(normalized.get("issues", []))
    if normalized.get("draw") is True:
        bits |= ISSUES["draw"]
    elif normalized.get("draw") not in (None, False):
        raise ValueError("Invalid draw flag.")
    if normalized.get("forfeit") is not None and type(normalized["forfeit"]) is not bool:
        raise ValueError("Invalid forfeit flag.")
    status = normalized.get("source_status")
    provider_code = code_for("provider_status", status) if status is not None else None
    return (
        checked_document(display, {"name", "league", "tournament", "match_type", "number_of_games"}, 2048),
        bits,
        provider_code,
        normalized.get("forfeit") is True,
    )


def match_state(old: dict) -> dict:
    """Normalize v5 state without inventing results, totals or timestamps."""
    status = old["status"]
    result = old.get("result")
    settlement = old.get("settlement_status", "pending")
    state = {
        "status": code_for("match_status", "finished" if status == "settled" else status),
        "result": code_for("result", result) if result is not None else None,
        "settlement_status": code_for("settlement_status", settlement),
        "settlement_at": old.get("settled_at")
        if settlement == "settled"
        else old.get("refunded_at")
        if settlement == "refunded"
        else None,
        "settlement_pool": old.get("settlement_pool"),
        "settlement_paid": old.get("settlement_paid"),
    }
    winner = old.get("winner_team_id")
    expected = (
        old.get("team_a_id") if result == "team_a_win" else old.get("team_b_id") if result == "team_b_win" else None
    )
    if winner != expected:
        raise ValueError("Winner and explicit result disagree; do not infer a new result.")
    if (result is None) != (old.get("result_recorded_at") is None):
        raise ValueError("Result timestamp mismatch.")
    if (settlement == "pending") != (state["settlement_at"] is None):
        raise ValueError("Settlement timestamp mismatch.")
    pool, paid, remainder = old.get("settlement_pool"), old.get("settlement_paid"), old.get("settlement_remainder")
    if any(v is not None for v in (pool, paid, remainder)):
        if any(v is None for v in (pool, paid, remainder)) or pool - paid != remainder:
            raise ValueError("Settlement reconciliation mismatch.")
    return state


def selected_side(team: str, team_a: str, team_b: str) -> int:
    if team_a == team_b or team not in (team_a, team_b):
        raise ValueError("Selected team is not a valid match participant.")
    return 1 if team == team_a else 2

def point_key_hash(member_id: int, season_id: int, reason: str, request_key: str) -> bytes:
    """Frozen v7 financial namespace; general audit hashes retain their old encoding."""
    key_hash(request_key)  # Same bounded key validation as existing callers.
    payload = {"namespace": "lolpick.points.v7", "member": member_id,
               "season": season_id, "reason": reason, "key": request_key}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).digest()

