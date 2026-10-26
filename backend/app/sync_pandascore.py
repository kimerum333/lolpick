"""Operator-only PandaScore discovery/import. No public synchronization endpoint."""

import argparse
import json
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.session import dispose_engine, get_engine
from app.integrations.pandascore import PandaScoreClient, PandaScoreError
from app.integrations.pandascore_mapping import normalize_match, normalize_series
from uuid import uuid4
from app.telemetry import emit_event, mirror_event, persist_http_event
from app.audit_journal import get_journal
from app.core.error_details import error_details
from app.services.pandascore_sync import SyncError, synchronize


def collect(client: PandaScoreClient, series_id: int) -> tuple[dict, list, datetime]:
    # Start time orders competing collectors; a slow old fetch cannot replace a newer run.
    started = datetime.now(timezone.utc)
    series = normalize_series(client.get_series(series_id), series_id)
    fixtures = [normalize_match(payload, series_id) for payload in client.list_matches(series_id)]
    return series, fixtures, started


def failure_log(season_id: str, exc, operation_id=None):
    request_id = operation_id or uuid4()
    from app.core.auth import AuthError
    from app.services.points import PointOperationError

    code = exc.code if isinstance(exc, (PandaScoreError, SyncError, AuthError, PointOperationError)) else "sync_failed"
    event = dict(
        created_at=datetime.now(timezone.utc).isoformat(),
        request_id=str(request_id),
        event_key=f"worker:{request_id}:finish",
        event_name="matches.synced",
        feature="matches",
        phase="failed",
        source="system",
        outcome="failure",
        severity="error",
        season_id=None,
        message={"code": code, "server_error": error_details(exc)},
    )
    try:
        emit_event(event)
        get_journal().replay(persist_http_event, limit=20)
    except Exception:
        mirror_event(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "severity": "error",
                "event_name": "audit.worker_unavailable",
                "request_id": str(request_id),
            }
        )
        # Exit remains nonzero; never pretend the durable record succeeded.


def sync_once(args, client: PandaScoreClient) -> dict:
    operation_id = uuid4()
    args.audit_operation_id = operation_id
    if not args.dry_run:
        emit_event(
            dict(
                created_at=datetime.now(timezone.utc).isoformat(),
                request_id=str(operation_id),
                event_key=f"worker:{operation_id}:start",
                event_name="worker.sync.started",
                phase="started",
                feature="matches",
                source="system",
                season_id=None,
                outcome="success",
                severity="info",
            )
        )
    series, fixtures, started = collect(client, args.series_id)
    if args.dry_run:
        return {
            "dryRun": True,
            "seriesId": args.series_id,
            "seriesName": series["name"],
            "received": len(fixtures),
            "readyToInsert": sum(len(row.teams) == 2 and row.starts_at is not None for row in fixtures),
            "requests": client.requests,
            "remainingRequests": client.remaining,
            "preview": [
                {
                    "id": f"pandascore-{row.external_id}",
                    "status": row.status,
                    "providerStatus": row.source_status,
                    "teams": [team["name"] for team in row.teams],
                    "startsAt": row.starts_at.isoformat() if row.starts_at else None,
                }
                for row in fixtures[:5]
            ],
        }
    with Session(get_engine()) as db, db.begin():
        result = synchronize(
            db,
            season_id=args.season_id,
            series=series,
            fixtures=fixtures,
            synced_at=started,
            create_slug=args.create_slug,
            create_name=args.create_name,
            initial_points=args.initial_points,
        )
    emit_event(
        dict(
            created_at=datetime.now(timezone.utc).isoformat(),
            request_id=str(operation_id),
            event_key=f"worker:{operation_id}:finish",
            event_name="worker.sync.completed",
            phase="finished",
            feature="matches",
            source="system",
            season_id=args.season_id,
            outcome="success",
            severity="info",
        )
    )
    get_journal().replay(persist_http_event, limit=20)
    return {**result, "requests": client.requests, "remainingRequests": client.remaining}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("leagues", help="List PandaScore LoL league IDs; does not access the DB.")
    series = commands.add_parser("series", help="List LoL series IDs; does not access the DB.")
    series.add_argument("--league-id", type=int)
    series.add_argument("--year", type=int)
    sync = commands.add_parser("sync", help="Collect a complete series and commit one DB transaction.")
    sync.add_argument("--season-id", required=True)
    sync.add_argument("--series-id", required=True, type=int)
    sync.add_argument("--create-slug", help="Explicitly allow creation of a new upcoming season.")
    sync.add_argument("--create-name")
    sync.add_argument("--initial-points", type=int, default=0)
    sync.add_argument("--dry-run", action="store_true", help="Fetch/normalize only; no DB access or logs.")
    sync.add_argument("--watch", action="store_true", help="Repeat until Ctrl+C; stop on the first error.")
    sync.add_argument("--interval", type=int, default=3600)
    args = parser.parse_args()
    if args.command == "sync" and (args.interval < 60 or (args.watch and args.dry_run)):
        parser.error("Use --interval >= 60; --watch and --dry-run cannot be combined.")
    try:
        while True:
            with PandaScoreClient() as client:
                if args.command == "leagues":
                    result = [
                        {"id": row["id"], "name": row.get("name"), "slug": row.get("slug")}
                        for row in client.list_leagues()
                    ]
                elif args.command == "series":
                    result = [
                        {
                            "id": row["id"],
                            "leagueId": row.get("league_id"),
                            "year": row.get("year"),
                            "name": row.get("full_name") or row.get("name"),
                            "slug": row.get("slug"),
                        }
                        for row in client.list_series(league_id=args.league_id, year=args.year)
                    ]
                else:
                    result = sync_once(args, client)
            output = result if isinstance(result, dict) else {"items": result}
            print(
                json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), **output}, ensure_ascii=True),
                flush=True,
            )
            if args.command != "sync" or not args.watch:
                return
            time.sleep(args.interval)
    except (PandaScoreError, SyncError) as exc:
        if args.command == "sync" and not args.dry_run:
            failure_log(args.season_id, exc, getattr(args, "audit_operation_id", None))
        print(
            json.dumps(
                {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "error": {"code": exc.code, "message": exc.message},
                }
            )
        )
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("PandaScore worker stopped.")
    except Exception as exc:
        if args.command == "sync" and not args.dry_run:
            failure_log(args.season_id, exc, getattr(args, "audit_operation_id", None))
        print(
            json.dumps(
                {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "error": {
                        "code": "sync_failed",
                        "message": "Check DB configuration and fixture data.",
                        "errorType": type(exc).__name__,
                    },
                }
            )
        )
        raise SystemExit(1) from None
    finally:
        dispose_engine()


if __name__ == "__main__":
    main()
