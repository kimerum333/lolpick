from compact_fixtures import internal, lookup, make_match, place_wager
from app.models import Season, Match, Member, Team

"""Synthetic fixtures only: no external account or live database is used."""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import func, select
from app.core.config import Settings, get_settings
from app.integrations.pandascore import PandaScoreClient, PandaScoreError
from app.integrations.pandascore_mapping import normalize_match, normalize_series
from app.models import PointTransaction, SystemLog
from app.services.catalog import serialize_match
from app.services.pandascore_sync import SyncError, synchronize
from app.sync_pandascore import collect, sync_once

GAME = {"id": 1, "slug": "league-of-legends", "name": "LoL"}
SERIES_ID = 700


def series_payload():
    return {
        "id": SERIES_ID,
        "league_id": 50,
        "year": 2026,
        "full_name": "Test Series 2026",
        "begin_at": "2026-10-01T00:00:00Z",
        "end_at": "2026-11-01T00:00:00Z",
        "videogame": GAME,
    }


def match_payload(identity=901, *, status="not_started", teams=(11, 22)):
    return {
        "id": identity,
        "serie_id": SERIES_ID,
        "serie": {"id": SERIES_ID},
        "videogame": GAME,
        "status": status,
        "scheduled_at": "2099-10-07T18:00:00+09:00",
        "begin_at": None,
        "name": "Team A vs Team B",
        "league": {"id": 50, "name": "Test League", "slug": "test"},
        "tournament": {"id": 60, "name": "Playoffs", "serie_id": SERIES_ID},
        "match_type": "best_of",
        "number_of_games": 3,
        "draw": False,
        "forfeit": False,
        "opponents": [
            {
                "type": "Team",
                "opponent": {
                    "id": team,
                    "name": f"Team {team}",
                    "acronym": f"T{team}",
                    "location": "KR",
                    "image_url": f"https://example.com/{team}.png",
                },
            }
            for team in teams
        ],
        "results": [{"team_id": team, "score": 2 if team == 11 else 1} for team in teams],
        "winner_id": 11 if status == "finished" and 11 in teams else None,
        "winner_type": "Team" if status == "finished" and 11 in teams else None,
    }


def provider(handler, **settings):
    return PandaScoreClient(
        Settings(_env_file=None, pandascore_token="synthetic-secret", **settings),
        transport=httpx.MockTransport(handler),
        sleep=lambda delay: None,
    )


def apply(db, *payloads, when=None, season_id="test-2026", **kwargs):
    db.commit()
    with db.begin():
        return synchronize(
            db,
            season_id=season_id,
            series=normalize_series(series_payload(), SERIES_ID),
            fixtures=[normalize_match(row, SERIES_ID) for row in payloads],
            synced_at=when or datetime.now(timezone.utc),
            create_slug="test",
            initial_points=100,
            **kwargs,
        )


def test_client_paginates_with_private_header_and_series_filter():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "api.pandascore.co"
        assert request.headers["authorization"] == "Bearer synthetic-secret"
        assert "token" not in request.url.params
        assert request.url.params["filter[serie_id]"] == str(SERIES_ID)
        return httpx.Response(
            200,
            json=[{"id": 901}, {"id": 902}] if len(requests) == 1 else [{"id": 903}],
            headers={"X-Rate-Limit-Remaining": "99"},
        )

    with provider(handler, pandascore_page_size=2) as api:
        assert [row["id"] for row in api.list_matches(SERIES_ID)] == [901, 902, 903]
        assert api.requests == 2 and api.remaining == 99
    assert [request.url.params["page"] for request in requests] == ["1", "2"]


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "pandascore_access_denied"),
        (403, "pandascore_access_denied"),
        (404, "pandascore_http_error"),
        (302, "pandascore_http_error"),
        (429, "pandascore_rate_limited"),
        (503, "pandascore_unavailable"),
    ],
)
def test_http_failures_never_expose_token_or_provider_body(status, code):
    with provider(
        lambda request: httpx.Response(status, text="synthetic-secret private response"), pandascore_retries=0
    ) as api:
        with pytest.raises(PandaScoreError) as error:
            api.list_leagues()
        assert error.value.code == code
        assert "synthetic-secret" not in str(error.value) + error.value.message
        assert api.requests == 1


def test_retry_after_and_transient_network_failures_are_bounded():
    calls, delays = ([], [])

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("private network message", request=request)
        if len(calls) == 2:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=[])

    with provider(handler) as api:
        api.sleep = delays.append
        assert api.list_leagues() == []
        assert api.requests == 3 and delays == [1, 2]


@pytest.mark.parametrize("retry_after", ["100", "Wed, 07 Oct 2026 00:00:00 GMT", "nan", "-1"])
def test_long_or_invalid_retry_after_does_not_block_worker(retry_after):
    with provider(lambda request: httpx.Response(429, headers={"Retry-After": retry_after})) as api:
        with pytest.raises(PandaScoreError, match="pandascore_rate_limited"):
            api.list_leagues()
        assert api.requests == 1


def test_exhausted_quota_prevents_next_page():
    with provider(
        lambda request: httpx.Response(200, json=[{"id": 1}], headers={"X-Rate-Limit-Remaining": "0"}),
        pandascore_page_size=1,
    ) as api:
        with pytest.raises(PandaScoreError, match="pandascore_rate_limited"):
            api.list_leagues()
        assert api.requests == 1


@pytest.mark.parametrize(
    "body,code",
    [
        ([{"id": True}], "invalid_identifier"),
        ({"id": 1}, "pandascore_invalid_response"),
        ([1], "pandascore_invalid_response"),
    ],
)
def test_malformed_responses_are_rejected(body, code):
    with provider(lambda request: httpx.Response(200, json=body)) as api:
        with pytest.raises(PandaScoreError, match=code):
            api.list_leagues()


def test_bad_json_is_rejected():
    with provider(lambda request: httpx.Response(200, text="{")) as api:
        with pytest.raises(PandaScoreError, match="pandascore_invalid_response"):
            api.list_leagues()


def test_repeated_ids_and_page_cap_abort_incomplete_collection():
    with provider(lambda request: httpx.Response(200, json=[{"id": 1}]), pandascore_page_size=1) as api:
        with pytest.raises(PandaScoreError, match="pandascore_pagination_changed"):
            api.list_leagues()
    with provider(
        lambda request: httpx.Response(200, json=[{"id": 1}]), pandascore_page_size=1, pandascore_max_pages=1
    ) as api:
        with pytest.raises(PandaScoreError, match="pandascore_page_limit"):
            api.list_leagues()


def test_missing_token_is_lazy_and_does_not_break_fastapi(client):
    assert client.get("/api/v1/health").status_code == 200
    with pytest.raises(PandaScoreError, match="pandascore_not_configured"):
        PandaScoreClient(Settings(_env_file=None, pandascore_token=""))


def test_discovery_uses_official_numeric_filters():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[series_payload()])

    with provider(handler) as api:
        assert api.list_series(league_id=50, year=2026)[0]["id"] == SERIES_ID
        assert api.get_series(SERIES_ID)["id"] == SERIES_ID
    assert requests[0].url.params["filter[league_id]"] == "50"
    assert requests[0].url.params["filter[year]"] == "2026"
    assert requests[1].url.params["filter[id]"] == str(SERIES_ID)


@pytest.mark.parametrize(
    "source,internal",
    [
        ("not_started", "scheduled"),
        ("postponed", "scheduled"),
        ("running", "live"),
        ("finished", "finished"),
        ("canceled", "cancelled"),
    ],
)
def test_normalization_status_utc_snapshot_and_scores(source, internal):
    result = normalize_match(match_payload(status=source), SERIES_ID)
    assert result.status == internal and result.source_status == source
    assert result.starts_at == datetime(2099, 10, 7, 9, tzinfo=timezone.utc)
    assert result.scores == {"11": 2, "22": 1}
    assert result.teams[0]["region"] == "" and result.teams[0]["location"] == "KR"
    assert result.winner_id == ("11" if source == "finished" else None)


def test_running_uses_actual_begin_time_and_never_infers_winner():
    payload = match_payload(status="running")
    payload["begin_at"] = "2099-10-07T10:00:00Z"
    assert normalize_match(payload, SERIES_ID).starts_at.hour == 10
    payload["status"] = "finished"
    payload["winner_id"] = None
    result = normalize_match(payload, SERIES_ID)
    assert result.winner_id is None and "finished_without_winner" in result.api_data["normalized"]["issues"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "settled"),
        ("serie_id", 999),
        ("id", -1),
        ("scheduled_at", "2099-10-07T09:00:00"),
        ("videogame", {"id": 2, "slug": "cs-go"}),
        ("number_of_games", True),
        ("results", [{"team_id": 11, "score": -1}]),
        ("results", [{"team_id": 999, "score": 1}]),
    ],
)
def test_invalid_or_unrelated_fixture_rejected(field, value):
    payload = match_payload()
    payload[field] = value
    with pytest.raises(PandaScoreError):
        normalize_match(payload, SERIES_ID)


def test_raw_snapshot_strips_secrets_and_unsafe_logo_and_draw_has_no_winner():
    payload = match_payload(status="finished")
    payload["draw"] = True
    payload["TOKEN"] = "private"
    payload["extra"] = {"access_token": "private", "keep": 1}
    payload["opponents"][0]["opponent"]["image_url"] = "javascript:alert(1)"
    result = normalize_match(payload, SERIES_ID)
    assert result.winner_id is None and result.teams[0]["logo"] is None
    assert "TOKEN" not in result.api_data["raw"]
    assert result.api_data["raw"]["extra"] == {"keep": 1}
    assert payload["TOKEN"] == "private"


def test_import_is_repeatable_without_activating_or_moving_money(db):
    first = apply(db, match_payload())
    second = apply(db, match_payload())
    assert first["created"] == second["updated"] == 1 and second["created"] == 0
    season = lookup(db, Season, "test-2026")
    assert season.status == "upcoming" and (not season.is_demo) and (season.initial_points == 100)
    assert season.external_series_id == str(SERIES_ID)
    assert db.scalar(select(func.count()).select_from(Match)) == 1
    assert db.scalar(select(func.count()).select_from(Member)) == 0
    assert db.scalar(select(func.count()).select_from(PointTransaction)) == 0
    log = db.scalar(select(SystemLog).where(SystemLog.event_name == "matches.synced"))
    assert log.source == "system" and log.context["series_id"] == SERIES_ID


def test_active_member_balance_and_initial_points_are_preserved(db):
    apply(db, match_payload())
    season = lookup(db, Season, "test-2026")
    season.status = "active"
    member_id = uuid4()
    db.add(
        Member(
            auth_user_id=member_id,
            google_sub="test-panda",
            current_season_id=internal(db, Season, season.id),
            current_points=73,
        )
    )
    db.commit()
    apply(db, match_payload(status="finished"))
    assert lookup(db, Member, member_id).current_points == 73
    assert lookup(db, Season, "test-2026").status == "active"
    assert lookup(db, Match, "pandascore-901").status == "finished"
    assert db.scalar(select(func.count()).select_from(PointTransaction)) == 0


def test_unknown_opponents_and_unscheduled_new_games_are_counted_not_fabricated(db):
    tbd, unscheduled = (match_payload(902, teams=()), match_payload(903))
    tbd["results"] = []
    unscheduled["scheduled_at"] = None
    result = apply(db, match_payload(), tbd, unscheduled)
    assert result["created"] == 3
    assert result["stored_tbd"] == result["stored_unscheduled"] == 1
    assert result["skipped_tbd"] == result["skipped_unscheduled"] == 0
    assert db.scalar(select(func.count()).select_from(Match)) == 3
    assert lookup(db, Match, "pandascore-902").team_a_id is None
    assert lookup(db, Match, "pandascore-903").starts_at is None


def test_team_order_changes_preserve_local_score_slots(db):
    apply(db, match_payload())
    apply(db, match_payload(status="finished", teams=(22, 11)))
    row = lookup(db, Match, "pandascore-901")
    assert (row.team_a_id, row.team_b_id, row.score_a, row.score_b, row.winner_team_id) == (
        internal(db, Team, "11"),
        internal(db, Team, "22"),
        2,
        1,
        internal(db, Team, "11"),
    )


def test_opponent_change_after_bet_is_flagged_and_does_not_retarget_bet(db):
    apply(db, match_payload())
    member_id = uuid4()
    db.add(Member(auth_user_id=member_id, google_sub="test-bettor"))
    db.flush()
    season = lookup(db, Season, "test-2026")
    season.status, season.initial_points = "active", 100
    place_wager(db, member_id=member_id, season_id=season.id, match_id="pandascore-901",
        selected_team_id="11", amount=10, idempotency_key="panda-test")
    db.commit()
    result = apply(db, match_payload(teams=(33, 44)))
    row = lookup(db, Match, "pandascore-901")
    assert result["conflicts"] == 1 and (row.team_a.external_team_id, row.team_b.external_team_id) == ("11", "22")
    assert row.team_a.name == "Team 11"
    assert "raw" not in row.api_data and "api_data" not in row.__table__.c
    assert "opponents_changed_after_betting" in row.api_data["normalized"]["issues"]


def test_opponent_change_before_betting_is_allowed(db):
    apply(db, match_payload())
    result = apply(db, match_payload(teams=(33, 44)))
    assert result["conflicts"] == 0
    assert lookup(db, Match, "pandascore-901").team_a_id == internal(db, Team, "33")


def test_settled_result_correction_is_visible_but_never_replaces_financial_result(db):
    payload = match_payload(status="finished")
    apply(db, payload)
    row = lookup(db, Match, "pandascore-901")
    row.status, row.settlement_at, row.settlement_status = ("finished", datetime.now(timezone.utc), "refunded")
    row.settlement_pool = row.settlement_paid = 0
    db.commit()
    assert apply(db, payload)["conflicts"] == 0
    payload["winner_id"] = 22
    payload["results"] = [{"team_id": 11, "score": 0}, {"team_id": 22, "score": 2}]
    assert apply(db, payload)["conflicts"] == 1
    row = lookup(db, Match, "pandascore-901")
    assert row.status == "finished" and row.winner_team_id == internal(db, Team, "11") and (row.score_a == 2)
    assert "finalized_result_changed" in row.api_data["normalized"]["issues"]


def test_refunded_cancelled_fixture_does_not_report_false_conflict(db):
    payload = match_payload(status="canceled")
    apply(db, payload)
    row = lookup(db, Match, "pandascore-901")
    row.settlement_at, row.settlement_status = (datetime.now(timezone.utc), "refunded")
    row.settlement_pool = row.settlement_paid = 0
    db.commit()
    assert apply(db, payload)["conflicts"] == 0
    assert apply(db, match_payload(status="running"))["conflicts"] == 1
    assert lookup(db, Match, "pandascore-901").status == "cancelled"


@pytest.mark.parametrize("status", ["running", "finished", "canceled"])
def test_regressed_status_or_cancelled_reappearance_is_not_silently_applied(db, status):
    apply(db, match_payload(status=status))
    assert apply(db, match_payload())["conflicts"] == 1
    assert lookup(db, Match, "pandascore-901").status != "scheduled"


def test_absent_matches_are_not_deleted_or_invented_as_cancelled(db):
    apply(db, match_payload(901), match_payload(902))
    apply(db, match_payload(901))
    assert lookup(db, Match, "pandascore-902").status == "scheduled"


def test_newer_collection_wins_and_series_cannot_bind_two_seasons(db):
    now = datetime.now(timezone.utc)
    apply(db, match_payload(), when=now)
    with pytest.raises(SyncError, match="stale_collection"):
        apply(db, match_payload(status="running"), when=now - timedelta(seconds=1))
    assert lookup(db, Match, "pandascore-901").status == "scheduled"
    with pytest.raises(SyncError, match="series_already_bound"):
        apply(db, match_payload(), season_id="another-2026")
    assert lookup(db, Season, "another-2026") is None


def test_atomic_batch_rolls_back_earlier_insert_when_later_id_conflicts(db):
    db.add(Season(public_code="other", slug="other", year=2026, name="Other", label="Other", status="upcoming"))
    db.flush()
    db.add(
        make_match(
            db,
            id="pandascore-902",
            season_id="other",
            api_source="legacy",
            external_match_id="902",
            round="",
            team_a_id="A",
            team_b_id="B",
            starts_at=datetime.now(timezone.utc),
            status="scheduled",
        )
    )
    db.commit()
    with pytest.raises(SyncError, match="match_id_conflict"):
        apply(db, match_payload(901), match_payload(902))
    assert lookup(db, Match, "pandascore-901") is None and lookup(db, Season, "test-2026") is None
    assert db.scalar(select(func.count()).select_from(SystemLog)) == 0


def test_collect_validates_entire_batch_before_database_and_dry_run_never_connects(monkeypatch):

    def handler(request):
        return httpx.Response(
            200, json=[series_payload()] if request.url.path.endswith("series") else [match_payload()]
        )

    from app import sync_pandascore

    monkeypatch.setattr(sync_pandascore, "get_engine", lambda: pytest.fail("Dry run must not access DB"))
    args = SimpleNamespace(series_id=SERIES_ID, dry_run=True)
    with provider(handler) as api:
        result = sync_once(args, api)
    assert result["dryRun"] and result["readyToInsert"] == 1 and (result["requests"] == 2)
    bad = match_payload(902)
    bad["status"] = "invalid"
    with provider(
        lambda request: httpx.Response(
            200, json=[series_payload()] if request.url.path.endswith("series") else [match_payload(), bad]
        )
    ) as api:
        with pytest.raises(PandaScoreError):
            collect(api, SERIES_ID)


def test_database_read_api_filters_details_and_never_exposes_raw_payload(client, db, monkeypatch):
    apply(db, match_payload(901), match_payload(902, status="finished"))
    monkeypatch.setattr(PandaScoreClient, "__init__", lambda *args, **kwargs: pytest.fail("Read API called provider"))
    response = client.get(
        "/api/v1/matches",
        params={
            "season_id": "test-2026",
            "api_source": "pandascore",
            "is_demo": "false",
            "team_id": "11",
            "status": "scheduled",
        },
    )
    assert response.status_code == 200 and response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["teams"] == ["11", "22"] and item["providerStatus"] == "not_started"
    assert item["league"]["name"] == "Test League" and item["numberOfGames"] == 3
    assert item["apiSyncedAt"] and (not item["bettingOpen"]) and (not item["dataIssues"])
    assert item["seasonStatus"] == "upcoming"
    assert item["pools"] == [0, 0] and item["apiSource"] == "pandascore"
    assert "raw" not in item and "api_data" not in item
    assert client.get("/api/v1/matches/pandascore-901").json() == item
    assert client.get("/api/v1/matches/missing").status_code == 404
    assert client.get("/api/v1/matches", params={"team_id": "missing"}).json()["total"] == 0
    assert len(client.get("/api/v1/teams", params={"season_id": "test-2026"}).json()) == 2


def test_operator_sync_commits_then_frontend_reads_same_data(client, db, monkeypatch):
    from app import sync_pandascore

    monkeypatch.setattr(sync_pandascore, "get_engine", lambda: db.get_bind())
    args = SimpleNamespace(
        series_id=SERIES_ID,
        season_id="cli-2026",
        dry_run=False,
        create_slug="cli",
        create_name="CLI Season",
        initial_points=50,
    )

    def handler(request):
        return httpx.Response(
            200, json=[series_payload()] if request.url.path.endswith("series") else [match_payload(status="finished")]
        )

    with provider(handler) as api:
        result = sync_once(args, api)
    assert result["created"] == 1 and result["requests"] == 2
    item = client.get("/api/v1/matches/pandascore-901").json()
    assert item["season"] == "cli-2026" and item["score"] == [2, 1] and (item["winner"] == "11")
    assert lookup(db, Season, "cli-2026").name == "CLI Season"


def test_postponement_stale_provider_data_and_tbd_disable_display_betting(db):
    now = datetime.now(timezone.utc)
    apply(db, match_payload(), when=now)
    row = lookup(db, Match, "pandascore-901")
    assert serialize_match(row, (0, 0), now, season_status="active").bettingOpen
    stale = serialize_match(row, (0, 0), now + timedelta(seconds=4501), season_status="active")
    assert not stale.bettingOpen and "provider_data_stale" in stale.dataIssues
    apply(db, match_payload(status="postponed"))
    row = lookup(db, Match, "pandascore-901")
    assert not serialize_match(row, (0, 0), now, season_status="active").bettingOpen
    payload = match_payload(teams=())
    payload["results"] = []
    apply(db, payload)
    row = lookup(db, Match, "pandascore-901")
    assert row.team_a_id is None and row.team_b_id is None
    assert not serialize_match(row, (0, 0), now, season_status="active").bettingOpen


def test_detail_view_is_logged_without_raw_url(client, monkeypatch):
    from app import telemetry

    recorded = []
    monkeypatch.setenv("SYSTEM_LOG_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(
        telemetry,
        "mirror_event",
        lambda event: recorded.append((event.copy(),)) if event.get("event_name") == "http.request" else None,
    )
    assert client.get("/api/v1/matches/missing").status_code == 404
    assert recorded[0][0]["feature"] == "matches" and recorded[0][0]["status_code"] == 404


def test_series_dates_and_game_are_validated():
    payload = copy.deepcopy(series_payload())
    payload["end_at"] = payload["begin_at"]
    with pytest.raises(PandaScoreError):
        normalize_series(payload, SERIES_ID)
