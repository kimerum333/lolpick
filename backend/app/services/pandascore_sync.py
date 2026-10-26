"""Atomic fixtures import and confirmed-result settlement in active seasons."""

import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.schema import MAX_POINTS
from app.integrations.pandascore_mapping import Fixture
from app.models import PointTransaction, Match, Season
from app.services.activity import record_event
from app.services.teams import preserve_team, preserve_source
from app.db.compact import compact_match_document, encode_issues, label_for, checked_document
from app.services.points import PointOperationError
from app.services.settlement import record_result, settle_locked_match


class SyncError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(code)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def fixture_values(fixture: Fixture, team_ids: tuple[str | None, str | None]) -> tuple[int | None, int | None, str | None]:
    score_a, score_b = (fixture.scores.get(identity) for identity in team_ids)
    if score_a is None or score_b is None:
        score_a, score_b = None, None
    winner = fixture.winner_id if fixture.winner_id in team_ids else None
    return score_a, score_b, winner


def store_display(match, fixture, issues):
    display, flags, provider, forfeit = compact_match_document(fixture.api_data)
    match.display_data = display
    match.issue_flags = flags | encode_issues(sorted(set(issues)))
    match.provider_status = label_for("provider_status", provider) if provider is not None else None
    match.is_forfeit = forfeit


def update_match(db: Session, match: Match, fixture: Fixture, synced_at: datetime) -> list[str]:
    issues = list(fixture.api_data["normalized"]["issues"])
    old_ids = (match.team_a.external_team_id if match.team_a else None,
               match.team_b.external_team_id if match.team_b else None)
    new_ids = tuple(team["id"] for team in fixture.teams) + (None,) * (2 - len(fixture.teams))
    protected = match.settlement_status in {"settled", "refunded"}
    # Order matters: a funded side=1 cannot silently turn into the old B team.
    changed_teams = set(new_ids) != set(old_ids)
    has_bets = db.scalar(select(func.count()).select_from(PointTransaction).where(
        PointTransaction.match_id == match.id, PointTransaction.reason == "bet_stake"
    )) > 0
    conflict = changed_teams and (has_bets or protected)
    if conflict:
        issues.append("opponents_changed_after_betting")
    if len(fixture.teams) != 2 and (has_bets or protected):
        issues.append("opponents_unavailable_for_existing_match")
    target_ids = old_ids if conflict or set(new_ids) == set(old_ids) else new_ids
    teams = {row["id"]: preserve_team(db, "pandascore", row, synced_at) for row in fixture.teams}
    score_a, score_b, winner = fixture_values(fixture, target_ids)
    if protected:
        expected_status = "cancelled" if match.result == "cancelled" else "finished"
        old_winner = (
            old_ids[0] if match.result == "team_a_win" else old_ids[1] if match.result == "team_b_win" else None
        )
        if (
            fixture.status != expected_status
            or (score_a, score_b, winner) != (match.score_a, match.score_b, old_winner)
            or ((match.result == "draw") != bool(fixture.api_data["normalized"].get("draw")))
        ):
            issues.append("finalized_result_changed")
    elif match.status == "cancelled" and fixture.status != "cancelled":
        issues.append("cancelled_match_reappeared")
    elif (match.status == "live" and fixture.status == "scheduled") or (
        match.status == "finished" and fixture.status in {"scheduled", "live"}
    ):
        issues.append("provider_status_regressed")
    elif not conflict:
        for slot, external in zip(("a", "b"), target_ids):
            setattr(match, f"team_{slot}_id", teams[external].id if external is not None else None)
        match.result, match.result_recorded_at = None, None
        match.status = fixture.status
        match.score_a, match.score_b = score_a, score_b
        if fixture.starts_at is not None:
            match.starts_at = fixture.starts_at
        match.round = fixture.api_data["normalized"]["round"]
    store_display(match, fixture, issues)
    match.api_synced_at = synced_at
    db.flush()
    db.expire(match, ["team_a", "team_b"])
    return issues


def synchronize(
    db: Session,
    *,
    season_id: str,
    series: dict,
    fixtures: list[Fixture],
    synced_at: datetime,
    create_slug: str | None = None,
    create_name: str | None = None,
    initial_points: int = 0,
    actor_member_id=None,
    request_id=None,
) -> dict:
    if synced_at.tzinfo is None:
        raise SyncError("invalid_sync_time", "Synchronization timestamps must include a timezone.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", season_id):
        raise SyncError("invalid_season_id", "Use a short lowercase season ID.")
    if type(initial_points) is not int or not 0 <= initial_points <= MAX_POINTS:
        raise SyncError("invalid_initial_points", "Use a nonnegative safe integer for initial points.")
    if len({fixture.external_id for fixture in fixtures}) != len(fixtures):
        raise SyncError("duplicate_fixture", "Duplicate provider fixtures were not applied.")
    if db.get_bind().dialect.name == "postgresql":
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(90520340)")):
            raise SyncError("sync_in_progress", "Another PandaScore synchronization is committing.")
    # One provider writer prevents cross-season team UPSERT inversions. Only the
    # target season is exclusively locked; unrelated season bets remain available.
    season = db.scalar(select(Season).where(Season.public_code == season_id).with_for_update().execution_options(populate_existing=True))
    seasons = list(db.scalars(select(Season).order_by(Season.id)))
    source = preserve_source(db, "pandascore")
    for row in seasons:
        if (
            row.public_code != season_id
            and row.api_source_id == source.id
            and row.external_series_id == str(series["series_id"])
        ):
            raise SyncError("series_already_bound", "This series is already assigned to another season.")
    if season is None:
        if not create_slug:
            raise SyncError("season_not_found", "Create the season explicitly with --create-slug.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", create_slug):
            raise SyncError("invalid_season_slug", "Use a short lowercase season slug.")
        if any(row.slug == create_slug and row.year == series["year"] for row in seasons):
            raise SyncError("season_slug_conflict", "Another season uses this slug and year.")
        name = create_name or series["name"]
        if not name or len(name) > 100:
            raise SyncError("invalid_season_name", "Season name must contain 1 to 100 characters.")
        season = Season(
            public_code=season_id,
            slug=create_slug,
            year=series["year"],
            name=name,
            label=None,
            status="upcoming",
            initial_points=initial_points,
            is_demo=False,
            starts_at=series["starts_at"],
            ends_at=series["ends_at"],
        )
        db.add(season)
        db.flush()
    if season.is_demo or season.year != series["year"]:
        raise SyncError("season_binding_conflict", "Demo seasons or a different year cannot be bound to this series.")
    if season.api_source_id is not None and (
        season.api_source_id != source.id or season.external_series_id != str(series["series_id"])
    ):
        raise SyncError("season_binding_conflict", "This season is already assigned to another series.")
    if season.api_synced_at and utc(season.api_synced_at) > utc(synced_at):
        raise SyncError("stale_collection", "A newer collection was already applied.")
    if db.scalar(
        select(func.count()).select_from(Match).where(Match.season_id == season.id, Match.api_source != "pandascore")
    ):
        raise SyncError("season_provider_conflict", "The season already contains matches from another provider.")
    season.api_source_id = source.id
    season.external_series_id = str(series["series_id"])
    season.external_league_id = str(series["league_id"])
    db.flush()  # Bind provider before any match trigger checks the season.
    counts = {
        "received": len(fixtures),
        "created": 0,
        "updated": 0,
        "skipped_tbd": 0,
        "stored_tbd": 0,
        "stored_unscheduled": 0,
        "skipped_unscheduled": 0,
        "conflicts": 0,
        "stale": 0,
        "settled": 0,
        "refunded": 0,
        "settlement_blocked": 0,
    }
    for fixture in sorted(fixtures, key=lambda item: int(item.external_id)):
        match = db.scalar(
            select(Match)
            .where(Match.api_source == "pandascore", Match.external_match_id == fixture.external_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if match is not None:
            if match.season_id != season.id or match.is_demo:
                raise SyncError("match_binding_conflict", "An existing fixture belongs to another season.")
            if match.api_synced_at and utc(match.api_synced_at) > utc(synced_at):
                counts["stale"] += 1
                continue
            issues = update_match(db, match, fixture, synced_at)
            if set(issues) & {
                "opponents_changed_after_betting",
                "finalized_result_changed",
                "provider_status_regressed",
                "cancelled_match_reappeared",
            }:
                counts["conflicts"] += 1
            counts["updated"] += 1
        else:
            match_id = f"pandascore-{fixture.external_id}"
            if db.scalar(select(Match.id).where(Match.public_code == match_id)) is not None:
                raise SyncError("match_id_conflict", "Generated match ID collides with existing data.")
            team_ids = tuple(team["id"] for team in fixture.teams) + (None,) * (2 - len(fixture.teams))
            score_a, score_b, winner = fixture_values(fixture, team_ids)
            pairs = [preserve_team(db, "pandascore", team, synced_at) for team in fixture.teams]
            pairs += [None] * (2 - len(pairs))
            match = Match(
                public_code=match_id,
                season_id=season.id,
                api_source_id=source.id,
                external_match_id=fixture.external_id,
                round=fixture.api_data["normalized"]["round"],
                team_a_id=pairs[0].id if pairs[0] else None,
                team_b_id=pairs[1].id if pairs[1] else None,
                starts_at=fixture.starts_at,
                status=fixture.status,
                score_a=score_a,
                score_b=score_b,
                api_synced_at=synced_at,
                is_demo=False,
            )
            issues = fixture.api_data["normalized"]["issues"]
            store_display(match, fixture, issues)
            db.add(match)
            counts["created"] += 1
        db.flush()
        counts["stored_tbd"] += int(len(fixture.teams) != 2)
        counts["stored_unscheduled"] += int(fixture.starts_at is None)
        benign = {"draw", "finished_without_winner"}
        if match.status == "cancelled":
            benign |= {"opponents_tbd", "start_time_unknown"}
        if set(issues) - benign or match.settlement_status != "pending":
            continue
        normalized = fixture.api_data["normalized"]
        result = (
            "cancelled"
            if match.status == "cancelled"
            else (
                "draw"
                if match.status == "finished" and normalized.get("draw")
                else "team_a_win"
                if match.status == "finished" and match.team_a is not None and fixture.winner_id == match.team_a.external_team_id
                else "team_b_win"
                if match.status == "finished" and match.team_b is not None and fixture.winner_id == match.team_b.external_team_id
                else None
            )
        )
        if result is None:
            continue  # Finished without a known winner is not evidence of a draw.
        record_result(match, result, score_a=match.score_a, score_b=match.score_b, recorded_at=synced_at)
        db.flush()
        if season.status == "active":
            try:
                # Preserve valid provider outcomes, but roll back all financial writes
                # for this match if its funded history cannot be reconciled.
                with db.begin_nested():
                    outcome = settle_locked_match(
                        db, match, season, actor_member_id=actor_member_id, request_id=request_id
                    )
                counts[outcome["settlementStatus"]] += 1
            except PointOperationError as exc:
                counts["settlement_blocked"] += 1
                record_event(
                    db,
                    "settlement.blocked",
                    season_id=season.id,
                    actor_member_id=actor_member_id,
                    request_id=request_id,
                    outcome="failure",
                    event_key=f"settlement:blocked:{uuid4()}",
                    context={"match_id": match.id, "error_code": exc.code, "error_type": type(exc).__name__},
                )
                match.issue_flags = encode_issues(sorted(set(issues) | {"settlement_blocked"}))
        db.flush()
    season.api_source_id = source.id
    season.external_series_id = str(series["series_id"])
    season.external_league_id = str(series["league_id"])
    season.api_synced_at = synced_at
    season.display_data = checked_document(
        {**(season.display_data or {}), "series_name": series["name"], "last_counts": counts},
        {"league", "series_name", "last_counts", "legacy_settings"}, 2048
    )
    if season.status == "upcoming":
        season.starts_at, season.ends_at = series["starts_at"], series["ends_at"]
    record_event(
        db,
        "matches.synced",
        season_id=season.id,
        actor_member_id=actor_member_id,
        request_id=request_id,
        event_key=f"pandascore:sync:{uuid4()}",
        context={
            "settlement_blocked": counts["settlement_blocked"],
            "count": counts["received"],
            "inserted": counts["created"],
            "updated": counts["updated"],
            "skipped": counts["skipped_tbd"] + counts["skipped_unscheduled"],
            "conflicts": counts["conflicts"],
            "series_id": series["series_id"],
        },
    )
    db.flush()
    return {"seasonId": season_id, "seriesId": series["series_id"], **counts}
