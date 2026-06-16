"""Minimal football-data.org v4 client for the World Cup competition.

Wraps the three free-tier endpoints we need (matches, standings, scorers) with
the throttle discipline documented by the API: every response carries
``X-Requests-Available-Minute`` and ``X-RequestCounter-Reset`` headers. We
self-throttle when the remaining budget drops to <= 1, retry once the counter
resets on HTTP 429, and refuse to retry HTTP 403 (a plan restriction that won't
fix itself).
"""

from __future__ import annotations

import logging
import time

import httpx

from khora_wc.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.football-data.org/v4"
COMPETITION = "WC"  # FIFA World Cup, competition id 2000
DEFAULT_TIMEOUT = 30.0


class PlanRestrictionError(RuntimeError):
    """Raised on HTTP 403 — the endpoint/field is not on the current plan."""


class FootballDataClient:
    """Synchronous football-data.org client with built-in self-throttling.

    Usable as a context manager so the underlying ``httpx.Client`` is closed::

        with FootballDataClient() as client:
            matches = client.get_matches()
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_429_retries: int = 3,
    ) -> None:
        self._token = token if token is not None else get_settings().data_football_token
        if not self._token:
            raise RuntimeError(
                "No football-data.org token configured (set DATA_FOOTBALL_TOKEN in .env)."
            )
        self._max_429_retries = max_429_retries
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={"X-Auth-Token": self._token},
            timeout=timeout,
        )

    # --- context manager -----------------------------------------------------
    def __enter__(self) -> FootballDataClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # --- low-level GET with throttle handling --------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET ``path`` and return parsed JSON, honoring the rate-limit headers.

        Retries HTTP 429 up to ``max_429_retries`` times after sleeping for the
        advertised reset window. Raises ``PlanRestrictionError`` on 403. After a
        successful response, sleeps if the per-minute budget is exhausted so the
        caller's *next* request doesn't immediately 429.
        """
        attempts = 0
        while True:
            response = self._client.get(path, params=params)

            if response.status_code == 429:
                attempts += 1
                if attempts > self._max_429_retries:
                    response.raise_for_status()
                wait = self._reset_seconds(response) + 1
                logger.warning(
                    "429 from %s; sleeping %ss before retry %d/%d",
                    path,
                    wait,
                    attempts,
                    self._max_429_retries,
                )
                time.sleep(wait)
                continue

            if response.status_code == 403:
                raise PlanRestrictionError(
                    f"403 from {path}: not available on the current plan. "
                    f"Body: {response.text[:200]}"
                )

            response.raise_for_status()
            self._self_throttle(response, path)
            return response.json()

    @staticmethod
    def _reset_seconds(response: httpx.Response) -> int:
        """Seconds until the request counter resets (default 60 if absent)."""
        raw = response.headers.get("X-RequestCounter-Reset", "60")
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 60

    def _self_throttle(self, response: httpx.Response, path: str) -> None:
        """Sleep until reset if the per-minute budget is down to its last slot."""
        raw = response.headers.get("X-Requests-Available-Minute")
        if raw is None:
            return
        try:
            available = int(raw)
        except (TypeError, ValueError):
            return
        if available <= 1:
            wait = self._reset_seconds(response) + 1
            logger.info(
                "Rate budget exhausted after %s (available=%d); sleeping %ss",
                path,
                available,
                wait,
            )
            time.sleep(wait)

    # --- public endpoints ----------------------------------------------------
    def get_matches(self, **params: object) -> dict:
        """GET all WC matches in one call.

        Accepts the documented filter params (``dateFrom``, ``dateTo``,
        ``status``, ``stage``, ``group``). Returns the raw response dict with a
        top-level ``matches`` list.
        """
        clean = {k: v for k, v in params.items() if v is not None}
        return self._get(f"/competitions/{COMPETITION}/matches", clean or None)

    def get_standings(self) -> dict:
        """GET the WC standings (group tables)."""
        return self._get(f"/competitions/{COMPETITION}/standings")

    def get_scorers(self, limit: int = 20) -> dict:
        """GET the WC top scorers, capped at ``limit`` players."""
        return self._get(f"/competitions/{COMPETITION}/scorers", {"limit": limit})
