"""Minimal API-Football (api-sports.io v3) client for World Cup lineups.

Wraps the two endpoints we need — ``/fixtures`` (list WC fixtures) and
``/fixtures/lineups`` (a fixture's starting XI) — with free-tier discipline:

* Auth via the ``api_football_token`` setting (env ``API_FOOTBALL_TOKEN``).
* Two provider shapes are supported (see ``PROVIDERS``); we default to the
  **direct** api-sports.io endpoint (base ``https://v3.football.api-sports.io``,
  header ``x-apisports-key``). If a key turns out to be RapidAPI-issued (a live
  call 403s), switch to ``provider="rapidapi"`` — same paths, different base
  (``https://api-football-v1.p.rapidapi.com/v3``) and headers
  (``x-rapidapi-key`` + ``x-rapidapi-host``). Only the base/headers change; the
  request and response handling are identical.
* The free tier allows 100 requests/day. Each response carries
  ``x-ratelimit-requests-remaining`` (daily budget left) and
  ``X-RateLimit-Remaining`` (per-minute burst). We expose the daily remaining
  via :pyattr:`Client.requests_remaining` so the orchestrator can stop before
  blowing the budget, self-throttle when the per-minute burst is exhausted, and
  retry once on HTTP 429.
* API-Football returns HTTP 200 even for application-level errors, packing them
  into a top-level ``errors`` object — we surface those as ``ApiFootballError``.
"""

from __future__ import annotations

import logging
import time

import httpx

from khora_wc.config import get_settings

logger = logging.getLogger(__name__)

WORLD_CUP_LEAGUE_ID = 1
WORLD_CUP_SEASON = 2026
DEFAULT_TIMEOUT = 30.0

# Daily request budget on the free tier; surfaced for cost-aware orchestration.
FREE_TIER_DAILY_LIMIT = 100

# The only thing that differs between providers is the base URL and the auth
# header(s). ``host`` is a format string filled in with the token at runtime.
_RAPIDAPI_HOST = "v3.football.api-sports.io"
PROVIDERS: dict[str, dict] = {
    # Direct api-sports.io (default).
    "direct": {
        "base_url": "https://v3.football.api-sports.io",
        "headers": {"x-apisports-key": "{token}"},
    },
    # RapidAPI-issued keys reach the same v3 API through RapidAPI's gateway.
    "rapidapi": {
        "base_url": "https://api-football-v1.p.rapidapi.com/v3",
        "headers": {"x-rapidapi-key": "{token}", "x-rapidapi-host": _RAPIDAPI_HOST},
    },
}
DEFAULT_PROVIDER = "direct"

# Backwards-compatible alias for the default direct base URL.
BASE_URL = PROVIDERS[DEFAULT_PROVIDER]["base_url"]


class ApiFootballError(RuntimeError):
    """Raised when API-Football returns an application-level ``errors`` payload."""


class ApiFootballClient:
    """Synchronous API-Football client with built-in rate-limit discipline.

    Usable as a context manager so the underlying ``httpx.Client`` is closed::

        with ApiFootballClient() as client:
            fixtures = client.get_fixtures()

    ``provider`` selects the base URL + auth headers (``"direct"`` by default,
    ``"rapidapi"`` for RapidAPI-issued keys).
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        provider: str = DEFAULT_PROVIDER,
        timeout: float = DEFAULT_TIMEOUT,
        max_429_retries: int = 2,
    ) -> None:
        self._token = token if token is not None else get_settings().api_football_token
        if not self._token:
            raise RuntimeError(
                "No API-Football token configured (set API_FOOTBALL_TOKEN in .env)."
            )
        if provider not in PROVIDERS:
            raise ValueError(
                f"Unknown provider {provider!r}; expected one of {sorted(PROVIDERS)}."
            )
        self._provider = provider
        spec = PROVIDERS[provider]
        headers = {k: v.format(token=self._token) for k, v in spec["headers"].items()}

        self._max_429_retries = max_429_retries
        # Daily budget remaining, learned from the most recent response header.
        # ``None`` until the first call tells us; treated as "unknown, proceed".
        self.requests_remaining: int | None = None
        self._client = httpx.Client(
            base_url=spec["base_url"],
            headers=headers,
            timeout=timeout,
        )

    # --- context manager -----------------------------------------------------
    def __enter__(self) -> ApiFootballClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # --- low-level GET with rate-limit handling ------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET ``path`` and return the parsed JSON ``response`` list.

        Retries HTTP 429 up to ``max_429_retries`` times after a short sleep,
        updates :pyattr:`requests_remaining` from the daily-budget header, and
        self-throttles when the per-minute burst budget is exhausted. Raises
        ``ApiFootballError`` when the payload carries a non-empty ``errors``
        object (API-Football returns 200 for those).
        """
        attempts = 0
        while True:
            response = self._client.get(path, params=params)

            if response.status_code == 429:
                attempts += 1
                if attempts > self._max_429_retries:
                    response.raise_for_status()
                wait = self._retry_after_seconds(response)
                logger.warning(
                    "429 from %s; sleeping %ss before retry %d/%d",
                    path,
                    wait,
                    attempts,
                    self._max_429_retries,
                )
                time.sleep(wait)
                continue

            response.raise_for_status()
            self._update_daily_budget(response)
            payload = response.json()

            errors = payload.get("errors")
            if errors:
                raise ApiFootballError(f"{path}: {errors}")

            self._self_throttle(response, path)
            return payload

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> int:
        """Seconds to wait after a 429 (Retry-After if present, else 60)."""
        raw = response.headers.get("Retry-After", "60")
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 60

    def _update_daily_budget(self, response: httpx.Response) -> None:
        """Record the daily requests remaining from the response headers."""
        raw = response.headers.get("x-ratelimit-requests-remaining")
        if raw is None:
            return
        try:
            self.requests_remaining = int(raw)
        except (TypeError, ValueError):
            return

    def _self_throttle(self, response: httpx.Response, path: str) -> None:
        """Pause briefly when the per-minute burst budget is down to its last slot.

        API-Football's per-minute limit header is ``X-RateLimit-Remaining``
        (distinct from the daily ``x-ratelimit-requests-remaining``). Sleeping a
        few seconds when it hits 0 keeps the caller's next request from 429-ing.
        """
        raw = response.headers.get("X-RateLimit-Remaining")
        if raw is None:
            return
        try:
            per_minute_left = int(raw)
        except (TypeError, ValueError):
            return
        if per_minute_left <= 0:
            logger.info(
                "Per-minute burst exhausted after %s; sleeping 6s", path
            )
            time.sleep(6)

    # --- public endpoints ----------------------------------------------------
    def get_fixtures(
        self,
        league: int = WORLD_CUP_LEAGUE_ID,
        season: int = WORLD_CUP_SEASON,
    ) -> list[dict]:
        """List World Cup fixtures (one API call).

        Returns the ``response`` list; each item carries ``fixture`` (id, date,
        status, venue), ``league``, and ``teams`` (home/away).
        """
        payload = self._get("/fixtures", {"league": league, "season": season})
        return payload.get("response") or []

    def get_lineups(self, fixture_id: int) -> list[dict]:
        """Fetch the starting lineups for a single fixture (one API call).

        Returns the ``response`` list — one entry per team, each with ``team``,
        ``formation``, ``startXI`` (and ``substitutes``/``coach``). Empty until
        the lineups are published (~40 min before kickoff).
        """
        payload = self._get("/fixtures/lineups", {"fixture": fixture_id})
        return payload.get("response") or []
