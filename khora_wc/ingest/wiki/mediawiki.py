"""Thin MediaWiki Action-API client (en.wikipedia.org).

Two reads are needed for the crawl:

* :func:`get_links` — the article-namespace (ns0) outgoing links of a page,
  paginated via ``plcontinue`` until exhausted.
* :func:`get_extract` — the page's full plain-text extract with redirects
  resolved, returned alongside the *resolved* title (so the caller dedups on the
  canonical title, not the redirect alias).

Both go through a shared :class:`httpx.Client` so a crawl reuses one connection
pool. Every request carries a descriptive User-Agent (Wikipedia requires bots to
identify themselves) and is followed by a small polite delay so a long crawl
stays well under the API's etiquette limits. Errors (network, non-2xx, malformed
JSON, MediaWiki ``error`` envelopes) are logged and turned into an empty/``None``
result rather than aborting the whole crawl.
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://en.wikipedia.org/w/api.php"

# Wikipedia asks automated clients to identify themselves with a descriptive UA
# (ideally with contact info); a request without one can be rate-limited harder.
USER_AGENT = "khora-world-cup/0.1 (PoC Wikipedia crawl; contact: local)"

# Seconds to sleep after each API request — be a polite citizen on a shared API.
# Wikipedia has no published hard rate limit for anonymous reads, but bursts can
# draw a 429; ~0.5s/request keeps a few-hundred-page crawl comfortably polite.
DEFAULT_DELAY = 0.5

# A single retry on HTTP 429, honoring ``Retry-After`` when the server sends it.
_RETRY_AFTER_FALLBACK = 5.0

# Article namespace. ns0 = real encyclopedia articles (not Talk:/Category:/etc.).
ARTICLE_NAMESPACE = 0


def _parse_retry_after(value: str | None) -> float:
    """Seconds to wait from a ``Retry-After`` header (delta-seconds form).

    Falls back to :data:`_RETRY_AFTER_FALLBACK` for a missing or non-numeric
    value (we don't bother parsing the rarely-used HTTP-date form).
    """
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
    return _RETRY_AFTER_FALLBACK


class MediaWikiClient:
    """Reusable MediaWiki Action-API client with a polite delay + UA.

    Construct one per crawl (or use it as a context manager) so the underlying
    HTTP connection pool is shared across every page fetched.
    """

    def __init__(
        self,
        *,
        api_url: str = API_URL,
        user_agent: str = USER_AGENT,
        delay: float = DEFAULT_DELAY,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_url = api_url
        self.delay = delay
        self._owns_client = client is None
        self._client = client or httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
        )

    # --- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        """Close the underlying HTTP client (only if we created it)."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> MediaWikiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- low-level request ---------------------------------------------------
    def _get(self, params: dict[str, str]) -> dict | None:
        """Issue one GET against the API; return parsed JSON or ``None``.

        Forces ``format=json``/``formatversion=2`` and always sleeps ``delay``
        afterwards (even on error) so retries stay polite. A MediaWiki ``error``
        envelope, a non-2xx status, a transport failure, or invalid JSON all log
        a warning and yield ``None`` so the crawl degrades rather than aborts.
        """
        query = {"format": "json", "formatversion": "2", **params}
        try:
            resp = self._client.get(self.api_url, params=query)
            # One backoff-and-retry on 429 (Too Many Requests). Wikipedia usually
            # sends a ``Retry-After`` header; honor it (falling back to a small
            # fixed wait) so a transient burst limit doesn't drop the page.
            if resp.status_code == 429:
                wait = _parse_retry_after(resp.headers.get("Retry-After"))
                logger.warning("429 from MediaWiki (%s); retrying in %.1fs.",
                               params.get("titles"), wait)
                time.sleep(wait)
                resp = self._client.get(self.api_url, params=query)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("MediaWiki request failed (%s): %s", params.get("titles"), exc)
            return None
        finally:
            if self.delay:
                time.sleep(self.delay)

        if isinstance(data, dict) and "error" in data:
            logger.warning(
                "MediaWiki API error for %s: %s",
                params.get("titles"),
                data["error"].get("info", data["error"]),
            )
            return None
        return data

    # --- public reads --------------------------------------------------------
    def get_links(self, title: str) -> list[str]:
        """Return the ns0 (article) outgoing links of ``title``.

        Pages with many links are paginated by the API; this follows the
        ``continue.plcontinue`` cursor until exhausted and concatenates every
        batch. Returns ``[]`` for a missing page or on any error.
        """
        links: list[str] = []
        plcontinue: str | None = None
        while True:
            params = {
                "action": "query",
                "prop": "links",
                "titles": title,
                "plnamespace": str(ARTICLE_NAMESPACE),
                "pllimit": "max",
                "redirects": "1",
            }
            if plcontinue:
                params["plcontinue"] = plcontinue

            data = self._get(params)
            if data is None:
                break

            for page in data.get("query", {}).get("pages", []):
                if page.get("missing"):
                    continue
                for link in page.get("links", []):
                    link_title = link.get("title")
                    if link_title:
                        links.append(link_title)

            cont = data.get("continue")
            if not cont or "plcontinue" not in cont:
                break
            plcontinue = cont["plcontinue"]

        return links

    def get_extract(self, title: str) -> tuple[str, str] | None:
        """Return ``(resolved_title, plaintext)`` for ``title``, or ``None``.

        ``explaintext`` gives the full article body as plain prose (no wiki
        markup); ``redirects=1`` resolves redirect aliases so the returned title
        is canonical. ``None`` is returned for a missing page, an empty extract,
        or any error.
        """
        params = {
            "action": "query",
            "prop": "extracts",
            "explaintext": "1",
            "redirects": "1",
            "titles": title,
        }
        data = self._get(params)
        if data is None:
            return None

        pages = data.get("query", {}).get("pages", [])
        if not pages:
            return None
        page = pages[0]
        if page.get("missing"):
            return None

        resolved_title = page.get("title") or title
        extract = (page.get("extract") or "").strip()
        if not extract:
            return None
        return resolved_title, extract
