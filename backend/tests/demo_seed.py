"""Synthetic catalog fixtures for isolated tests; never loaded by the application."""

from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.models import Match, Season
from sqlalchemy import select
from app.services.teams import preserve_team, preserve_source

SAMPLE_API_TEAMS = {
    "GEN": {"id": "GEN", "name": "Gen.G", "region": "LCK", "color": "#a88a47"},
    "T1": {"id": "T1", "name": "T1", "region": "LCK", "color": "#d94347"},
    "HLE": {"id": "HLE", "name": "Hanwha Life", "region": "LCK", "color": "#ee7d36"},
    "BLG": {"id": "BLG", "name": "Bilibili Gaming", "region": "LPL", "color": "#35989c"},
    "G2": {"id": "G2", "name": "G2 Esports", "region": "LEC", "color": "#5b6374"},
    "TES": {"id": "TES", "name": "Top Esports", "region": "LPL", "color": "#bb4932"},
}


def seed_demo(db: Session, anchor: datetime | None = None) -> dict[str, int]:
    anchor = anchor or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        raise ValueError("Seed anchor must include a timezone.")
    counts = {"seasons": 0, "matches": 0}
    for season_id, slug, name, status in [
        ("worlds-2026", "worlds", "Worlds", "active"),
        ("msi-2026", "msi", "MSI", "finished"),
    ]:
        existing = db.scalar(select(Season).where(Season.public_code == season_id))
        if existing is not None and not existing.is_demo:
            raise ValueError("Demo season IDs overlap with real data; seed aborted.")
        if existing is None:
            db.add(
                Season(
                    public_code=season_id,
                    slug=slug,
                    year=2026,
                    name=name,
                    label=f"2026 {name}",
                    status=status,
                    is_demo=True,
                    initial_points=0,
                )
            )
            counts["seasons"] += 1
    db.flush()
    rows = [
        ("demo-w1", "worlds-2026", "GEN", "T1", 2, "scheduled", 6480000, 3920000, None, None),
        ("demo-w2", "worlds-2026", "HLE", "BLG", 5, "scheduled", 2340000, 1860000, None, None),
        ("demo-w3", "worlds-2026", "G2", "TES", 26, "scheduled", 1420000, 2580000, None, None),
        ("demo-w4", "worlds-2026", "T1", "G2", -1, "live", 3800000, 1200000, (1, 0), None),
        ("demo-w5", "worlds-2026", "GEN", "BLG", -24, "finished", 4600000, 2400000, (2, 1), "GEN"),
        ("demo-m1", "msi-2026", "GEN", "T1", -1920, "finished", 8000000, 4000000, (3, 2), "GEN"),
    ]
    for match_id, season_id, team_a, team_b, hours, status, pool_a, pool_b, score, winner in rows:
        existing = db.scalar(select(Match).where(Match.public_code == match_id))
        if existing is not None:
            if not existing.is_demo:
                raise ValueError("Demo match IDs overlap with real data; seed aborted.")
            continue
        season = db.scalar(select(Season).where(Season.public_code == season_id))
        source = preserve_source(db, "demo")
        a = preserve_team(db, "demo", SAMPLE_API_TEAMS[team_a], anchor)
        b = preserve_team(db, "demo", SAMPLE_API_TEAMS[team_b], anchor)
        db.add(
            Match(
                public_code=match_id,
                season_id=season.id,
                api_source_id=source.id,
                external_match_id=match_id,
                round="결승 · BO5" if season_id == "msi-2026" else "스위스 스테이지 · BO3",
                team_a_id=a.id,
                team_b_id=b.id,
                starts_at=anchor + timedelta(hours=hours),
                status=status,
                demo_pool_a=pool_a,
                demo_pool_b=pool_b,
                score_a=score[0] if score else None,
                score_b=score[1] if score else None,
                is_demo=True,
                result=("team_a_win" if winner == team_a else "team_b_win") if winner else None,
                result_recorded_at=anchor if winner else None,
            )
        )
        counts["matches"] += 1
    db.flush()
    return counts
