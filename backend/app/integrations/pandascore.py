"""PandaScore LoL fixtures client. GET only; token never appears in query strings."""
import time
from typing import Callable
import httpx

from app.core.config import Settings, get_settings
from app.integrations.http_json import read_json, InvalidUpstreamJSON

BASE_URL = "https://api.pandascore.co"


class PandaScoreError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(code)


def positive_id(value: object) -> int:
    if type(value) is not int or value < 1:
        raise PandaScoreError("invalid_identifier", "PandaScore IDs must be positive integers.")
    return value


class PandaScoreClient:
    def __init__(self, settings: Settings | None = None, *, transport=None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.settings = settings or get_settings()
        secret = self.settings.pandascore_token
        token = secret.get_secret_value().strip() if secret else ""
        if not token or any(character.isspace() for character in token):
            raise PandaScoreError("pandascore_not_configured", "Set PANDASCORE_TOKEN in backend/.env.")
        self.client = httpx.Client(base_url=BASE_URL, timeout=self.settings.pandascore_timeout,
                                   follow_redirects=False, trust_env=False, transport=transport,
                                   headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                            "Accept-Encoding": "identity"})
        self.sleep = sleep
        self.clock = clock
        self.deadline = clock() + self.settings.pandascore_collection_budget_seconds
        self.requests = 0
        self.remaining: int | None = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def remaining_budget(self):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise PandaScoreError("pandascore_collection_timeout", "Collection time budget was exceeded.")
        return remaining

    def wait_retry(self, delay):
        if delay >= self.remaining_budget():
            raise PandaScoreError("pandascore_collection_timeout", "Collection time budget was exceeded.")
        self.sleep(delay)
        self.remaining_budget()

    def _get(self, path: str, params: dict) -> list[dict]:
        if path not in {"/lol/leagues", "/lol/series", "/lol/matches"}:
            raise PandaScoreError("unsupported_endpoint", "Unsupported PandaScore endpoint.")
        for attempt in range(self.settings.pandascore_retries + 1):
            if self.remaining == 0:
                raise PandaScoreError("pandascore_rate_limited", "PandaScore request quota is exhausted.")
            try:
                self.requests += 1
                with self.client.stream("GET", path, params=params,
                        timeout=min(self.settings.pandascore_timeout, self.remaining_budget())) as response:
                    status, headers = response.status_code, response.headers
                    payload = (
                        read_json(response, 5_000_000, check_deadline=self.remaining_budget)
                        if status == 200 else None
                    )
                self.remaining_budget()
            except InvalidUpstreamJSON:
                raise PandaScoreError("pandascore_invalid_response", "PandaScore returned invalid or oversized JSON.") from None
            except httpx.HTTPError:
                if attempt < self.settings.pandascore_retries:
                    self.wait_retry(min(2 ** attempt, self.settings.pandascore_max_retry_delay))
                    continue
                raise PandaScoreError("pandascore_unavailable", "PandaScore request failed or timed out.") from None
            header = headers.get("X-Rate-Limit-Remaining")
            if header is not None and header.isdecimal():
                self.remaining = int(header)
            if status in (401, 403):
                raise PandaScoreError("pandascore_access_denied", "Check the PandaScore token and subscribed coverage.")
            if status == 429 or status >= 500:
                delay = float(2 ** attempt)
                retry_after = headers.get("Retry-After")
                if retry_after is not None:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = self.settings.pandascore_max_retry_delay + 1
                if (attempt < self.settings.pandascore_retries and self.remaining != 0
                        and 0 <= delay <= self.settings.pandascore_max_retry_delay):
                    self.wait_retry(delay)
                    continue
                code = "pandascore_rate_limited" if status == 429 else "pandascore_unavailable"
                raise PandaScoreError(code, "PandaScore quota or availability prevented collection.")
            if status != 200:
                raise PandaScoreError("pandascore_http_error", "PandaScore returned an unexpected HTTP status.")
            if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
                raise PandaScoreError("pandascore_invalid_response", "Expected a list of PandaScore objects.")
            return payload
        raise RuntimeError("Unreachable retry state.")

    def _list(self, path: str, filters: dict | None = None) -> list[dict]:
        rows, seen = [], set()
        for page in range(1, self.settings.pandascore_max_pages + 1):
            batch = self._get(path, {**(filters or {}), "sort": "id", "page": page,
                                     "per_page": self.settings.pandascore_page_size})
            if len(batch) > self.settings.pandascore_page_size:
                raise PandaScoreError("pandascore_invalid_response", "Pagination returned too many rows.")
            for row in batch:
                identity = positive_id(row.get("id"))
                if identity in seen:
                    raise PandaScoreError("pandascore_pagination_changed", "Repeated IDs during pagination; retry the collection.")
                seen.add(identity)
                rows.append(row)
            if len(batch) < self.settings.pandascore_page_size:
                return rows
        raise PandaScoreError("pandascore_page_limit", "Increase PANDASCORE_MAX_PAGES; incomplete data was not applied.")

    def list_leagues(self) -> list[dict]:
        return self._list("/lol/leagues")

    def list_series(self, *, league_id: int | None = None, year: int | None = None) -> list[dict]:
        filters = {}
        if league_id is not None:
            filters["filter[league_id]"] = positive_id(league_id)
        if year is not None:
            if type(year) is not int or year < 2000:
                raise PandaScoreError("invalid_year", "Use a valid year.")
            filters["filter[year]"] = year
        return self._list("/lol/series", filters)

    def get_series(self, series_id: int) -> dict:
        series_id = positive_id(series_id)
        rows = self._list("/lol/series", {"filter[id]": series_id})
        if len(rows) != 1 or rows[0]["id"] != series_id:
            raise PandaScoreError("series_not_found", "The requested LoL series was not found.")
        return rows[0]

    def list_matches(self, series_id: int) -> list[dict]:
        return self._list("/lol/matches", {"filter[serie_id]": positive_id(series_id)})
