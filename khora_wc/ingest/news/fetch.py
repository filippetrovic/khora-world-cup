"""News fetch orchestrator: fetch -> dedup -> transform -> write.

Three modes:

* ``recent`` (default): the curated RSS set (FOX WC + BBC + ESPN + Sky +
  Guardian) plus a rolling Google News ``when:7d`` search.
* ``backfill``: walk :func:`backfill_windows` newest-first, one Google News
  search per weekly window, back to ~May 11; plus a best-effort NewsData pull.
* ``all``: ``recent`` then ``backfill``.

Dedup happens *before* writing. Seen ``external_id`` values are persisted in
``data/state/news_seen.json``; an article already in that set is skipped
(never re-written, so the downstream remember worker never re-extracts it).
``limit`` caps how many *new* docs we write per run to control LLM cost.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from khora_wc.config import Settings, get_settings
from khora_wc.contract import RememberDoc, write_doc
from khora_wc.ingest.news.google_news import (
    SOURCE_NAME as GOOGLE_SOURCE,
    backfill_windows,
    fetch_google_news,
    new_resolver_client,
    resolve_article_url,
)
from khora_wc.ingest.news.newsdata import fetch_newsdata
from khora_wc.ingest.news.rss import Article, DEFAULT_FEEDS, fetch_feed
from khora_wc.ingest.news.transform import (
    article_to_doc,
    canonicalize_url,
    external_id_for,
    is_wc_relevant,
)

logger = logging.getLogger(__name__)

# Names of the curated RSS feeds whose content is already 100% World Cup and so
# bypass the relevance filter. Kept in sync with ``rss.DEFAULT_FEEDS``.
_WC_ONLY_SOURCES = frozenset(f.name for f in DEFAULT_FEEDS if f.wc_only)

GOOGLE_QUERY = "FIFA World Cup 2026"
STATE_FILENAME = "news_seen.json"


# --- seen-state persistence --------------------------------------------------
def _state_path(settings: Settings) -> Path:
    return settings.state_dir / STATE_FILENAME


def load_seen(settings: Settings) -> set[str]:
    """Load the set of already-written external_ids (empty if no state yet)."""
    path = _state_path(settings)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Could not read seen-state %s: %s; starting fresh.", path, exc)
        return set()
    return set(data.get("seen", []))


def save_seen(settings: Settings, seen: set[str]) -> None:
    """Persist the seen set atomically (sorted for stable diffs)."""
    path = _state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"seen": sorted(seen)}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


# --- fetch phases ------------------------------------------------------------
# Google News URLs are resolved lazily in ``run()`` (see ``resolve=False``),
# so we only pay the per-link decode cost for entries we actually keep.
def _fetch_recent() -> list[Article]:
    """Curated RSS feeds + a rolling 7-day Google News search."""
    articles: list[Article] = []
    for feed in DEFAULT_FEEDS:
        articles.extend(fetch_feed(feed))
    articles.extend(fetch_google_news(GOOGLE_QUERY, when="7d", resolve=False))
    return articles


def _fetch_backfill() -> list[Article]:
    """Weekly Google News windows (newest-first) + best-effort NewsData."""
    articles: list[Article] = []
    for after, before in backfill_windows():
        articles.extend(
            fetch_google_news(GOOGLE_QUERY, after=after, before=before, resolve=False)
        )
    articles.extend(fetch_newsdata(GOOGLE_QUERY))
    return articles


def _gather(mode: str) -> list[Article]:
    if mode == "recent":
        return _fetch_recent()
    if mode == "backfill":
        return _fetch_backfill()
    if mode == "all":
        return _fetch_recent() + _fetch_backfill()
    raise ValueError(f"Unknown mode {mode!r}; expected recent|backfill|all.")


# --- orchestration -----------------------------------------------------------
def run(mode: str = "recent", limit: int | None = 60, *, full: bool = False) -> dict:
    """Fetch, dedup, transform, and write new WC news docs.

    Returns a counts dict::

        {written, skipped, filtered_out, total_seen, examples}

    ``written`` new docs, ``skipped`` already-seen articles, ``filtered_out``
    non-WC / empty-body articles. ``limit`` (None = unlimited) caps ``written``
    to control downstream extract cost. ``examples`` is a short list of written
    file paths for self-test / CLI display.

    ``full`` (default False) ignores the persisted ``news_seen.json`` set so
    every fetched article is re-written to the inbox (in-run dedup across feeds
    still applies); the seen-state is refreshed afterwards. Default keeps the
    skip-if-already-seen behavior.
    """
    settings = get_settings()
    seen = load_seen(settings)
    initial_seen = set(seen)
    # In ``full`` mode we ignore the persisted seen-set for the skip decision
    # but still record everything we write so the state is refreshed and in-run
    # cross-feed duplicates are collapsed via ``handled_this_run``.
    skip_set: set[str] = set() if full else seen

    articles = _gather(mode)
    logger.info("Mode %s gathered %d raw articles.", mode, len(articles))

    written = 0
    skipped = 0
    filtered_out = 0
    examples: list[str] = []

    # Track ids already handled *this run* (in-memory) so duplicates across
    # feeds within a single run also dedup, even before state is persisted.
    handled_this_run: set[str] = set()

    # One shared client for lazy Google News URL resolution (created on first
    # use; closed in ``finally``).
    resolver = None
    try:
        for article in articles:
            if limit is not None and written >= limit:
                # Hit the write cap — remaining articles are intentionally left
                # for a future run rather than counted as skipped/filtered.
                break

            assume_relevant = article.source_name in _WC_ONLY_SOURCES

            # Google News URLs arrive unresolved (opaque news.google.com links).
            # Resolving is the expensive step, so apply the cheap relevance
            # filter on title+body FIRST and only resolve entries we'd keep.
            if not assume_relevant and "news.google.com" in article.url:
                if not is_wc_relevant(f"{article.title}\n{article.body}"):
                    filtered_out += 1
                    continue
                if resolver is None:
                    resolver = new_resolver_client()
                article.url = resolve_article_url(resolver, article.url)

            # Cheap pre-check: if the canonical id is already known, skip
            # without building a full RememberDoc.
            canonical = canonicalize_url(article.url)
            if not canonical:
                filtered_out += 1
                continue
            ext_id = external_id_for(canonical)
            if ext_id in skip_set or ext_id in handled_this_run:
                skipped += 1
                continue

            doc: RememberDoc | None = article_to_doc(
                article, assume_relevant=assume_relevant
            )
            if doc is None:
                filtered_out += 1
                continue

            # The doc's own external_id is authoritative (it canonicalizes too);
            # re-check against state in case the pre-check URL differed.
            if doc.external_id in skip_set or doc.external_id in handled_this_run:
                skipped += 1
                continue

            path = write_doc(settings, doc)
            handled_this_run.add(doc.external_id)
            seen.add(doc.external_id)
            written += 1
            if len(examples) < 5:
                examples.append(str(path))
    finally:
        if resolver is not None:
            resolver.close()

    if seen != initial_seen:
        save_seen(settings, seen)

    counts = {
        "written": written,
        "skipped": skipped,
        "filtered_out": filtered_out,
        "total_seen": len(seen),
        "examples": examples,
    }
    logger.info("News run complete: %s", {k: v for k, v in counts.items() if k != "examples"})
    return counts
