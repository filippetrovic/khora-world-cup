"""Google News RSS search fetcher.

Google News exposes a date-scoped RSS search endpoint (no API key). Each result
``link`` is an opaque ``news.google.com/rss/articles/CBMi...`` URL, NOT the
publisher URL. Modern Google News no longer 30x-redirects these to the
publisher — they resolve to a Google-hosted JS interstitial. To recover the
real article URL we replay Google's own decode call: fetch the article page for
its per-article signature/timestamp, then POST those to the internal
``batchexecute`` ``Fbv4je`` endpoint, which returns the canonical publisher
URL. If that fails for an entry we fall back to following the redirect, and if
that also stays on google.com we keep the Google URL (the transform layer will
still dedup on it).

Date scoping is done inside the query string via ``when:7d`` (rolling) or the
absolute ``after:YYYY-MM-DD before:YYYY-MM-DD`` operators. :func:`backfill_windows`
yields weekly ``(after, before)`` tuples walking newest-first back to the start
of the tournament context window (~May 11, 2026).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote_plus

import feedparser
import httpx

from khora_wc.ingest.news.rss import Article, USER_AGENT, parse_entries

logger = logging.getLogger(__name__)

GOOGLE_NEWS_BASE = "https://news.google.com/rss/search"
SOURCE_NAME = "Google News"

# Google News internal "decode opaque article id -> publisher URL" endpoint.
_BATCHEXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_DECODE_RPCID = "Fbv4je"
# Signature / timestamp embedded in the article interstitial page.
_SIG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')
# Resolved URL inside the (escaped) batchexecute JSON response.
_RESOLVED_RE = re.compile(r'\\"garturlres\\",\\"(http[^\\"]+)\\"')

# Start of the tournament context window we backfill toward.
BACKFILL_START = date(2026, 5, 11)

# Per-link redirect resolution should be quick; we have many links.
_REDIRECT_TIMEOUT = 8.0


def _build_url(query: str, after: date | None, before: date | None, when: str | None) -> str:
    """Assemble the Google News RSS search URL with date operators folded in."""
    parts = [query]
    if when:
        parts.append(f"when:{when}")
    if after:
        parts.append(f"after:{after.isoformat()}")
    if before:
        parts.append(f"before:{before.isoformat()}")
    q = quote_plus(" ".join(parts))
    return f"{GOOGLE_NEWS_BASE}?q={q}&hl=en-US&gl=US&ceid=US:en"


def _article_id(google_url: str) -> str | None:
    """Extract the opaque ``CBMi...`` id from a Google News article URL."""
    if "/articles/" not in google_url:
        return None
    return google_url.split("/articles/", 1)[1].split("?", 1)[0]


def _decode_via_batchexecute(client: httpx.Client, google_url: str) -> str | None:
    """Recover the publisher URL by replaying Google's own decode RPC.

    Fetches the interstitial page for its signature/timestamp, then POSTs them
    to the ``Fbv4je`` batchexecute endpoint. Returns the publisher URL or
    ``None`` on any failure (caller falls back to redirect-following).
    """
    gn_id = _article_id(google_url)
    if not gn_id:
        return None
    try:
        page = client.get(google_url, follow_redirects=True)
        html = page.text
        sig_m = _SIG_RE.search(html)
        ts_m = _TS_RE.search(html)
        if not (sig_m and ts_m):
            return None

        inner = json.dumps(
            [
                "garturlreq",
                [
                    ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
                     None, None, None, None, None, 0, 1],
                    "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
                ],
                gn_id,
                int(ts_m.group(1)),
                sig_m.group(1),
            ]
        )
        payload = json.dumps([[[_DECODE_RPCID, inner]]])
        resp = client.post(
            _BATCHEXECUTE_URL,
            data={"f.req": payload},
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
            },
        )
        match = _RESOLVED_RE.search(resp.text)
        return match.group(1) if match else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("batchexecute decode failed for %s: %s", google_url, exc)
        return None


def _follow_redirect(client: httpx.Client, google_url: str) -> str | None:
    """Fallback: follow the redirect; return the URL only if it left google.com."""
    for method in ("HEAD", "GET"):
        try:
            resp = client.request(method, google_url, follow_redirects=True)
        except httpx.HTTPError as exc:
            logger.debug("Redirect %s for %s failed: %s", method, google_url, exc)
            continue
        final = str(resp.url)
        if final and "news.google.com" not in final:
            return final
    return None


def _resolve_url(client: httpx.Client, google_url: str) -> str:
    """Resolve a Google News URL to its publisher URL, best-effort.

    Tries the batchexecute decode first (works for the modern opaque format),
    then a plain redirect follow, then keeps the Google URL unchanged.
    """
    if "news.google.com" not in google_url:
        return google_url  # already a publisher URL
    return (
        _decode_via_batchexecute(client, google_url)
        or _follow_redirect(client, google_url)
        or google_url
    )


def _resolve_links(articles: list[Article]) -> None:
    """Resolve every article's Google URL to its publisher URL, in place."""
    if not articles:
        return
    resolved = 0
    with httpx.Client(
        timeout=_REDIRECT_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for article in articles:
            article.url = _resolve_url(client, article.url)
            if "news.google.com" not in article.url:
                resolved += 1
    logger.info(
        "Google News: resolved %d/%d links to publisher URLs", resolved, len(articles)
    )


def new_resolver_client() -> httpx.Client:
    """An httpx client configured for URL resolution (caller closes it)."""
    return httpx.Client(timeout=_REDIRECT_TIMEOUT, headers={"User-Agent": USER_AGENT})


def resolve_article_url(client: httpx.Client, google_url: str) -> str:
    """Public single-URL resolver, for callers that resolve lazily.

    Resolution is the expensive part of Google News ingestion (a page fetch +
    decode RPC per link). Orchestrators that dedup/filter most entries away can
    use this to resolve *only* the URLs they actually intend to keep, instead
    of paying for the whole feed up front.
    """
    return _resolve_url(client, google_url)


def fetch_google_news(
    query: str,
    after: date | None = None,
    before: date | None = None,
    when: str | None = None,
    *,
    resolve: bool = True,
) -> list[Article]:
    """Search Google News RSS and return articles.

    With ``resolve=True`` (default) each article's opaque Google URL is decoded
    to its publisher URL before returning. Pass ``resolve=False`` to defer that
    (cost-heavy) step to the caller via :func:`resolve_article_url` — the
    returned articles then still carry their ``news.google.com`` URLs.

    ``when`` (e.g. ``"7d"``) and ``after``/``before`` are mutually compatible
    but you normally use one or the other. Network failures are non-fatal: a
    feed that fails to parse yields ``[]``; a link that fails to resolve keeps
    its Google URL.
    """
    url = _build_url(query, after, before, when)
    try:
        parsed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as exc:
        logger.warning("Google News fetch failed for %r: %s", query, exc)
        return []

    articles = parse_entries(parsed, SOURCE_NAME)
    logger.info(
        "Google News %r (after=%s before=%s when=%s): %d entries",
        query,
        after,
        before,
        when,
        len(articles),
    )
    if resolve:
        _resolve_links(articles)
    return articles


def backfill_windows(
    start: date = BACKFILL_START,
    end: date | None = None,
    step_days: int = 7,
    overlap_days: int = 1,
) -> list[tuple[date, date]]:
    """Weekly ``(after, before)`` windows from ``start`` to ``end``, newest-first.

    Windows overlap by ``overlap_days`` so an article published on a boundary is
    not missed. ``before`` is exclusive in Google's semantics, so the newest
    window ends one day past today to include today's articles.
    """
    if end is None:
        end = datetime.now(UTC).date()

    windows: list[tuple[date, date]] = []
    before = end + timedelta(days=1)
    while before > start:
        after = before - timedelta(days=step_days)
        if after < start:
            after = start
        windows.append((after, before))
        # Advance the window back, keeping a small overlap.
        before = after + timedelta(days=overlap_days)
        if after == start:
            break
    return windows
