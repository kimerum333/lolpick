"""Provider objects -> internal fixtures. Never infer a winner from scores."""
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.integrations.pandascore import PandaScoreError, positive_id

STATUS_MAP = {"not_started": "scheduled", "postponed": "scheduled", "running": "live",
              "finished": "finished", "canceled": "cancelled"}
SECRET_KEYS = {"token", "access_token", "refresh_token", "api_key", "authorization", "password", "client_secret"}


def data_error() -> PandaScoreError:
    return PandaScoreError("pandascore_invalid_fixture", "PandaScore fixture fields are inconsistent or invalid.")


def timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise data_error()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise data_error() from None
    return parsed.astimezone(timezone.utc)


def safe_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme == "https" and parts.hostname and not parts.username and not parts.password:
            return value
    except ValueError:
        pass
    return None


def string(value: object, default: str = "", limit: int = 120) -> str:
    return value[:limit] if isinstance(value, str) and value else default


def sanitized_payload(value: object, depth: int = 0):
    if depth > 30:
        raise data_error()
    if isinstance(value, dict):
        return {key: sanitized_payload(item, depth + 1) for key, item in value.items()
                if key.lower() not in SECRET_KEYS}
    if isinstance(value, list):
        return [sanitized_payload(item, depth + 1) for item in value]
    return copy.deepcopy(value)


def competition(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {"id": str(positive_id(value.get("id"))), "name": string(value.get("name")),
            "slug": string(value.get("slug"))}


def assert_lol(payload: dict):
    game = payload.get("videogame")
    if not isinstance(game, dict) or game.get("id") != 1 or game.get("slug") != "league-of-legends":
        raise PandaScoreError("pandascore_wrong_game", "Only League of Legends fixtures are accepted.")


@dataclass(frozen=True)
class Fixture:
    external_id: str
    source_status: str
    status: str
    starts_at: datetime | None
    teams: tuple[dict, ...]
    scores: dict[str, int]
    winner_id: str | None
    api_data: dict


def normalize_match(payload: dict, series_id: int) -> Fixture:
    assert_lol(payload)
    external_id = str(positive_id(payload.get("id")))
    serie = payload.get("serie")
    embedded_id = serie.get("id") if isinstance(serie, dict) else None
    if payload.get("serie_id", embedded_id) != series_id or (embedded_id is not None and embedded_id != series_id):
        raise PandaScoreError("pandascore_wrong_series", "A fixture belongs to another series.")
    source_status = payload.get("status")
    if not isinstance(source_status, str) or source_status not in STATUS_MAP:
        raise data_error()
    starts = timestamp(payload.get("begin_at")) if source_status in {"running", "finished"} else None
    starts = starts or timestamp(payload.get("scheduled_at")) or timestamp(payload.get("begin_at"))
    opponents = payload.get("opponents", [])
    if not isinstance(opponents, list) or len(opponents) > 2:
        raise data_error()
    teams = []
    for entry in opponents:
        if not isinstance(entry, dict) or entry.get("type") != "Team":
            raise data_error()
        opponent = entry.get("opponent")
        if opponent is None:
            continue
        if not isinstance(opponent, dict):
            raise data_error()
        team_id = str(positive_id(opponent.get("id")))
        team = {"id": team_id, "acronym": string(opponent.get("acronym")) or None,
                "location": string(opponent.get("location")) or None,
                "region": "", "color": "#64748b", "logo": safe_url(opponent.get("image_url"))}
        # Preserve absence through normalization; INSERT has an ID fallback,
        # but an update must not replace an existing name with that fallback.
        name = string(opponent.get("name"))
        if name:
            team["name"] = name
        teams.append(team)
    if len({team["id"] for team in teams}) != len(teams):
        raise data_error()
    scores = {}
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise data_error()
    for result in results:
        if not isinstance(result, dict) or "team_id" not in result:
            raise data_error()
        team_id = str(positive_id(result["team_id"]))
        score = result.get("score")
        if type(score) is not int or not 0 <= score <= 32_767 or team_id in scores:
            raise data_error()
        scores[team_id] = score
    winner_id = payload.get("winner_id")
    if winner_id is not None:
        if payload.get("winner_type") != "Team":
            raise data_error()
        winner_id = str(positive_id(winner_id))
    if source_status != "finished":
        winner_id = None
    known_ids = {team["id"] for team in teams}
    if len(teams) == 2 and (set(scores) - known_ids or (winner_id is not None and winner_id not in known_ids)):
        raise data_error()
    number = payload.get("number_of_games")
    if number is not None and (type(number) is not int or not 0 <= number <= 100):
        raise data_error()
    match_type = string(payload.get("match_type")) or None
    tournament = competition(payload.get("tournament"))
    stage = tournament["name"] if tournament else ""
    format_name = f"BO{number}" if match_type == "best_of" and number else f"{number} games" if number else ""
    issues = []
    if len(teams) != 2:
        issues.append("opponents_tbd")
    if starts is None:
        issues.append("start_time_unknown")
    if source_status == "postponed":
        issues.append("postponed")
    if source_status == "finished" and winner_id is None:
        issues.append("finished_without_winner")
    if payload.get("draw") is True:
        issues.append("draw")
        winner_id = None
    normalized = {"name": string(payload.get("name"), f"Match {external_id}"),
                  "round": " · ".join(part for part in (stage, format_name) if part)[:120],
                  "league": competition(payload.get("league")), "tournament": tournament,
                  "match_type": match_type, "number_of_games": number,
                  "source_status": source_status, "forfeit": payload.get("forfeit") is True,
                  "draw": payload.get("draw") is True, "issues": issues}
    return Fixture(external_id, source_status, STATUS_MAP[source_status], starts, tuple(teams), scores, winner_id,
                   {"teams": teams, "normalized": normalized, "raw": sanitized_payload(payload)})


def normalize_series(payload: dict, series_id: int) -> dict:
    assert_lol(payload)
    if positive_id(payload.get("id")) != series_id:
        raise data_error()
    year = payload.get("year")
    if type(year) is not int or year < 2000:
        raise data_error()
    starts, ends = timestamp(payload.get("begin_at")), timestamp(payload.get("end_at"))
    if starts and ends and starts >= ends:
        raise data_error()
    name = string(payload.get("full_name"), string(payload.get("name"), f"Series {series_id}"), 100)
    return {"series_id": series_id, "league_id": positive_id(payload.get("league_id")), "year": year,
            "name": name, "starts_at": starts, "ends_at": ends, "raw": sanitized_payload(payload)}
