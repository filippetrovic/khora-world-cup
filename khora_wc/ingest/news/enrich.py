"""Fetch and extract full article text for thin news bodies.

Many feeds carry only a headline (Google News) or a one-sentence summary (BBC)
in their RSS body — the actual article prose lives on the publisher page. This
module fetches the resolved publisher ``source_url`` and extracts the main
article text with :mod:`trafilatura` (boilerplate-stripped), replacing the
article body only when the extraction is genuinely richer than what we had.

Enrichment is best-effort and never raises: paywalls, bot-blocks, and timeouts
all fall back to the original body. It only fetches when the body is *thin*
(a title-echo, à la :func:`~khora_wc.ingest.news.transform._is_title_echo`, or
shorter than :data:`ENRICH_THRESHOLD`) and the URL is a real publisher
``http(s)`` URL — unresolved ``news.google.com`` links are skipped.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import httpx
import trafilatura

from khora_wc.ingest.news.rss import Article, USER_AGENT
from khora_wc.ingest.news.transform import MIN_BODY_CHARS, _is_title_echo

logger = logging.getLogger(__name__)

# Bodies shorter than this (and not title-echoes) are treated as thin and worth
# a publisher-page fetch. ~800 chars is roughly a couple of real paragraphs;
# RSS summaries (BBC ~200) fall well under it, full articles (FOX ~4000) over.
ENRICH_THRESHOLD = 800

# Per-page fetch budget. Article pages are heavier than the redirect resolution,
# so a slightly more generous timeout than google_news' redirect timeout.
_FETCH_TIMEOUT = 12.0


def _needs_enrichment(article: Article) -> bool:
    """True if the article body is thin enough to warrant a publisher fetch."""
    body = (article.body or "").strip()
    title = (article.title or "").strip()
    return _is_title_echo(title, body) or len(body) < ENRICH_THRESHOLD


def _is_publisher_url(url: str) -> bool:
    """True for a real ``http(s)`` publisher URL we can fetch.

    Unresolved Google News links (``news.google.com``) carry no article text,
    so we never fetch them here.
    """
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return False
    return "news.google.com" not in url


def _fetch_html(url: str, client: httpx.Client) -> str | None:
    """Fetch the publisher page HTML; return ``None`` on any network failure."""
    try:
        resp = client.get(url, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug("Article fetch failed for %s: %s", url, exc)
        return None
    return resp.text or None


def _extract_article(html: str) -> str | None:
    """Extract the main article text from page HTML, whitespace-normalized.

    ``favor_recall`` keeps more of the body for sites with sparse markup;
    comments/tables are dropped as boilerplate. Returns ``None`` if trafilatura
    finds no usable article text.
    """
    try:
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
    except Exception as exc:  # trafilatura/lxml parse blow-up — non-fatal
        logger.debug("Article extraction raised: %s", exc)
        return None
    if not text:
        return None
    # Collapse runs of blank lines but keep paragraph breaks for readability.
    paragraphs = [line.strip() for line in text.splitlines()]
    cleaned = "\n".join(p for p in paragraphs if p)
    return cleaned or None


def enrich_body(article: Article, *, client: httpx.Client | None = None) -> bool:
    """Replace ``article.body`` with full article text when the body is thin.

    Returns ``True`` if the body was enriched (replaced), ``False`` otherwise
    (already rich enough, unfetchable URL, fetch/extract failure, or the
    extraction was not meaningfully better than the existing body). Never
    raises — failures degrade gracefully to the original body.

    Pass a shared ``client`` to reuse one connection pool across a batch; if
    omitted a short-lived client is created for the single call.
    """
    if not _needs_enrichment(article):
        return False
    if not _is_publisher_url(article.url):
        logger.debug("Skipping enrichment (non-publisher URL): %s", article.url)
        return False

    owns_client = client is None
    if owns_client:
        client = httpx.Client(
            timeout=_FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
    try:
        html = _fetch_html(article.url, client)
        if html is None:
            return False
        extracted = _extract_article(html)
    finally:
        if owns_client:
            client.close()

    return _apply_extraction(article, extracted)


def _apply_extraction(article: Article, extracted: str | None) -> bool:
    """Set ``article.body`` to ``extracted`` if it's a meaningful improvement.

    Shared by the sync and async paths so both honor the same accept rules:
    extraction must clear ``MIN_BODY_CHARS`` and be strictly longer than the
    current body (otherwise the existing RSS summary is just as good and we
    avoid needless churn). Returns ``True`` when the body was replaced.
    """
    if not extracted or len(extracted) < MIN_BODY_CHARS:
        logger.debug("No usable extraction for %s", article.url)
        return False

    current = (article.body or "").strip()
    if len(extracted) <= len(current):
        logger.debug(
            "Extraction (%d) not longer than body (%d) for %s",
            len(extracted),
            len(current),
            article.url,
        )
        return False

    logger.info(
        "Enriched %s: body %d -> %d chars", article.url, len(current), len(extracted)
    )
    article.body = extracted
    return True


def new_enrich_client() -> httpx.Client:
    """An httpx client configured for article-page fetches (caller closes it)."""
    return httpx.Client(timeout=_FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT})


# --- async / concurrent enrichment -------------------------------------------
# Enriching ~2k GDELT URLs one-at-a-time (each a page fetch + trafilatura parse)
# would take hours. The async path fetches up to ``concurrency`` pages at once
# behind an asyncio.Semaphore, with a per-domain lock so we never hammer a
# single publisher with parallel requests. trafilatura's ``extract`` is sync/
# CPU-bound, so it runs in a thread (``asyncio.to_thread``) to avoid blocking
# the event loop. This turns hours into minutes for a 2k batch.

# Min seconds between successive requests to the *same* domain (politeness).
_PER_DOMAIN_DELAY = 0.75


def _domain_of(url: str) -> str:
    """Lowercased host of ``url`` (empty string if unparseable)."""
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


async def _fetch_html_async(url: str, client: httpx.AsyncClient) -> str | None:
    """Async publisher-page fetch; ``None`` on any network failure (non-fatal).

    Failures are logged at INFO with the publisher *domain* and reason so they
    surface in the tailable news-fetch log (the bulk GDELT enrich path can hit
    many paywalls / bot-blocks; seeing which domains fail is useful).
    """
    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.info(
            "Enrichment fetch failed: domain=%s reason=%s", _domain_of(url), exc
        )
        return None
    return resp.text or None


async def _enrich_one_async(
    article: Article,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    domain_locks: dict[str, asyncio.Lock],
) -> bool:
    """Enrich a single article's body in place; never raises.

    Skips already-rich bodies and non-publisher URLs (same gate as the sync
    path), then fetches + extracts under the global concurrency semaphore and a
    per-domain lock that spaces same-domain requests by ``_PER_DOMAIN_DELAY``.
    """
    if not _needs_enrichment(article) or not _is_publisher_url(article.url):
        return False

    domain = _domain_of(article.url)
    lock = domain_locks.setdefault(domain, asyncio.Lock())

    async with sem:
        async with lock:
            html = await _fetch_html_async(article.url, client)
            # Hold the domain lock across the politeness sleep so concurrent
            # tasks for the same domain queue rather than burst.
            await asyncio.sleep(_PER_DOMAIN_DELAY)
        if html is None:
            return False
        # trafilatura is CPU-bound and sync — offload so it doesn't block the
        # event loop while other fetches are in flight.
        extracted = await asyncio.to_thread(_extract_article, html)

    return _apply_extraction(article, extracted)


async def enrich_bodies(
    articles: list[Article], *, concurrency: int = 12
) -> int:
    """Concurrently body-enrich ``articles`` in place; return the count enriched.

    Fetches up to ``concurrency`` publisher pages at once behind a semaphore,
    with a per-domain lock for politeness, extracting full article text with
    trafilatura. Each article that needs it and succeeds has its ``body``
    replaced; failures (paywall, bot-block, timeout, no extraction) leave the
    title-only body untouched. Never raises — per-URL errors are swallowed.

    This is the bulk path for the GDELT source, where every article arrives
    body-less and there can be thousands of URLs.
    """
    if not articles:
        return 0

    sem = asyncio.Semaphore(concurrency)
    domain_locks: dict[str, asyncio.Lock] = {}
    async with httpx.AsyncClient(
        timeout=_FETCH_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        results = await asyncio.gather(
            *(
                _enrich_one_async(a, client, sem, domain_locks)
                for a in articles
            ),
            return_exceptions=True,
        )

    enriched = 0
    for result in results:
        if isinstance(result, BaseException):
            logger.debug("Async enrich task errored (non-fatal): %s", result)
            continue
        if result:
            enriched += 1
    logger.info("Async enrich: %d/%d articles enriched", enriched, len(articles))
    return enriched
