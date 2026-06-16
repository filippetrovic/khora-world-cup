"""GDELT 2.0 DOC API fetcher (FREE, keyless).

GDELT indexes worldwide online news and exposes a public DOC 2.0 search API at
``https://api.gdeltproject.org/api/v2/doc/doc`` — no key, no signup. We use it
as the *bulk* news source: a fan-out of query variants across daily time
windows yields thousands of WC-2026 article URLs after dedup.

GDELT returns only article *metadata* (url, title, seendate, domain, language,
country) — never the body — so every :class:`~khora_wc.ingest.news.rss.Article`
this module produces has ``body=""`` and must be body-enriched downstream (see
:func:`~khora_wc.ingest.news.enrich.enrich_bodies`).

The DOC API caps a single response at 250 records, so reach comes from the
*window x query* fan-out rather than deep pagination: each (query, day) pair is
one request returning up to 250 of that day's matches.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, date, datetime, timedelta

import httpx

from khora_wc.ingest.news.rss import Article

logger = logging.getLogger(__name__)

DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
SOURCE_PREFIX = "GDELT"

# DOC API hard cap per response; reach comes from the window x query fan-out.
MAX_RECORDS = 250
_TIMEOUT = 30.0

# GDELT's public DOC API documents a hard limit of ONE request every 5 seconds
# per IP; over it the API returns HTTP 429 "Please limit requests to one every 5
# seconds". The fan-out fires hundreds of these, so we self-pace: every
# ``fetch_gdelt`` waits until at least this many seconds have elapsed since the
# previous call. A small margin over 5s absorbs clock jitter. The throttle is
# process-wide (module-level) and thread-safe so concurrent callers serialize.
_MIN_REQUEST_INTERVAL = 5.5
_throttle_lock = threading.Lock()
_last_request_at = 0.0


def _throttle() -> None:
    """Block until ``_MIN_REQUEST_INTERVAL`` has passed since the last request.

    Logs the pacing wait at INFO so a tailed log shows *normal* self-pacing
    (distinct from the WARNING emitted on an actual 429 backoff in
    :func:`fetch_gdelt`).
    """
    global _last_request_at
    with _throttle_lock:
        wait = _MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            logger.info("GDELT throttle: pacing sleep %.2fs (5s/IP budget)", wait)
            time.sleep(wait)
        _last_request_at = time.monotonic()

# Start of the tournament context window we backfill toward (matches
# google_news.BACKFILL_START so the corpus has a single, coherent date floor).
BACKFILL_START = date(2026, 5, 11)

# A browser-ish UA. GDELT is permissive but a real UA avoids edge-case blocks.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Query fan-out. Each variant is an independent DOC search; together they cast a
# wider net than the plain title query alone (team/host/topic angles surface
# articles the bare phrase misses). ``sourcelang:english`` keeps the corpus
# English; the quoted phrase is GDELT's exact-phrase operator. Variants overlap
# heavily on purpose — dedup downstream collapses the overlap, and the union is
# what gets us to ~2k unique URLs.
QUERY_VARIANTS: tuple[str, ...] = (
    'sourcelang:english "World Cup 2026"',
    'sourcelang:english "FIFA World Cup 2026"',
    'sourcelang:english "World Cup 2026" qualifying',
    'sourcelang:english "World Cup 2026" draw',
    'sourcelang:english "World Cup 2026" group',
    'sourcelang:english "World Cup 2026" squad',
    'sourcelang:english "World Cup 2026" USA',
    'sourcelang:english "World Cup 2026" Canada',
    'sourcelang:english "World Cup 2026" Mexico',
    'sourcelang:english "World Cup 2026" host',
    'sourcelang:english "World Cup 2026" tickets',
    'sourcelang:english "World Cup 2026" stadium',
)

# Single, simplest variant — handy for smoke tests and the default code path.
DEFAULT_QUERY = "FIFA World Cup 2026"

# GDELT seendate looks like "20260601T120000Z" (compact ISO-ish, UTC).
_SEENDATE_FMT = "%Y%m%dT%H%M%SZ"
# ...and the API's startdatetime/enddatetime want "YYYYMMDDHHMMSS" (UTC).
_WINDOW_FMT = "%Y%m%d%H%M%S"


def _parse_seendate(raw: str | None) -> datetime | None:
    """Parse a GDELT ``seendate`` (``20260601T120000Z``) to aware UTC datetime."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, _SEENDATE_FMT).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _to_window_str(d: date, *, end_of_day: bool = False) -> str:
    """Render a ``date`` as a GDELT ``YYYYMMDDHHMMSS`` UTC window bound."""
    moment = datetime(
        d.year, d.month, d.day,
        23 if end_of_day else 0,
        59 if end_of_day else 0,
        59 if end_of_day else 0,
        tzinfo=UTC,
    )
    return moment.strftime(_WINDOW_FMT)


def _article_from_record(record: dict) -> Article | None:
    """Map one GDELT ``articles[]`` record to an :class:`Article`.

    ``body`` is always empty (GDELT carries no article text); ``source_name``
    is ``GDELT:<domain>`` so the provenance is visible while the per-article
    publisher is still identifiable. ``raw_id`` is the url (GDELT has no stable
    article id of its own). Returns ``None`` for records missing url or title.
    """
    url = (record.get("url") or "").strip()
    title = (record.get("title") or "").strip()
    if not url or not title:
        return None
    domain = (record.get("domain") or "").strip()
    return Article(
        title=title,
        body="",
        url=url,
        published_at=_parse_seendate(record.get("seendate")),
        source_name=f"{SOURCE_PREFIX}:{domain}" if domain else SOURCE_PREFIX,
        raw_id=url,
    )


def fetch_gdelt(query: str, start: date, end: date) -> list[Article]:
    """Search GDELT DOC 2.0 for ``query`` between ``start`` and ``end`` (UTC days).

    Returns up to :data:`MAX_RECORDS` articles (body-less — enrich downstream)
    sorted newest-first by GDELT. ``start`` is inclusive at 00:00:00 UTC and
    ``end`` inclusive at 23:59:59 UTC. Never raises: any network/parse failure
    logs and returns ``[]`` so a fan-out keeps going.
    """
    params = {
        "query": query,
        "mode": "ArtList",
        "maxrecords": str(MAX_RECORDS),
        "format": "json",
        "sort": "datedesc",
        "startdatetime": _to_window_str(start),
        "enddatetime": _to_window_str(end, end_of_day=True),
    }
    logger.info("GDELT query=%r window=%s..%s fetching", query, start, end)
    # One retry: the 5s/IP budget is shared, so a 429 can slip through even with
    # self-pacing if another process raced us. Backing off one full interval and
    # retrying once recovers the window without aborting the whole fan-out.
    for attempt in range(2):
        _throttle()
        try:
            resp = httpx.get(
                DOC_API_URL,
                params=params,
                headers={"User-Agent": _USER_AGENT},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 429 and attempt == 0:
                logger.warning(
                    "GDELT 429 (rate-limited) for %r (%s..%s); backing off %.1fs and retrying once.",
                    query, start, end, _MIN_REQUEST_INTERVAL,
                )
                time.sleep(_MIN_REQUEST_INTERVAL)
                continue
            if resp.status_code == 429:
                logger.warning(
                    "GDELT 429 again for %r (%s..%s) after retry; giving up this window.",
                    query, start, end,
                )
            resp.raise_for_status()
            # GDELT occasionally returns an HTML error page with a 200 status
            # when a query is malformed; guard the JSON decode.
            payload = resp.json()
            break
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "GDELT fetch failed for %r (%s..%s): %s", query, start, end, exc
            )
            return []
    else:  # pragma: no cover - retry loop always breaks or returns above
        return []

    records = payload.get("articles") or []
    articles: list[Article] = []
    for record in records:
        article = _article_from_record(record)
        if article:
            articles.append(article)
    logger.info(
        "GDELT %r (%s..%s): %d/%d articles",
        query,
        start,
        end,
        len(articles),
        len(records),
    )
    return articles


def daily_windows(
    start: date = BACKFILL_START, end: date | None = None
) -> list[tuple[date, date]]:
    """Single-day ``(day, day)`` windows from ``start`` to ``end`` inclusive.

    Newest-first so the freshest news lands in the inbox first. One day per
    window keeps each (query, day) request comfortably under the 250-record cap
    on busy days; the union across days + query variants is what reaches ~2k.
    """
    if end is None:
        end = datetime.now(UTC).date()
    windows: list[tuple[date, date]] = []
    day = end
    while day >= start:
        windows.append((day, day))
        day -= timedelta(days=1)
    return windows
