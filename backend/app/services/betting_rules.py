"""One eligibility rule for public display and financial writes."""
from datetime import datetime, timezone
from app.core.config import get_settings
from app.services.sessions import utc


def betting_closed_reason(match, *, now=None, season_status=None):
    now = utc(now or datetime.now(timezone.utc))
    season = match.season_row
    status = season.status if season_status is None else season_status
    if match.is_demo or season.is_demo:
        return "demo_match"
    if status != "active":
        return "season_not_active"
    if match.status != "scheduled" or match.result is not None or match.settlement_status != "pending":
        return "match_closed"
    if match.team_a_id is None or match.team_b_id is None:
        return "teams_tbd"
    if match.starts_at is None:
        return "unscheduled"
    if utc(match.starts_at) <= now:
        return "betting_deadline_passed"
    if match.api_source == "pandascore" and (match.api_synced_at is None or
        (now - utc(match.api_synced_at)).total_seconds() > get_settings().pandascore_max_staleness_seconds):
        return "provider_data_stale"
    if match.provider_status != "not_started" or match.issue_flags:
        return "provider_data_unconfirmed"
    return None
