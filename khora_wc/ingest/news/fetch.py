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
from khora_wc.ingest.news.enrich import enrich_body, new_enrich_client
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
# Each phase yields one or more *labeled groups* ``(label, articles)``. A label
# is a string like ``"recent"`` or ``"backfill:2026-05-11..2026-05-18"``. The
# orchestrator applies its write cap *per group* so that a single recent-news
# burst cannot starve the older weekly backfill windows: every week from the
# backfill start gets its own budget and therefore contributes to the inbox.
#
# Google News URLs are resolved lazily in ``run()`` (see ``resolve=False``), so
# we only pay the per-link decode cost for entries we actually keep.
Group = tuple[str, list[Article]]


def _fetch_recent() -> list[Group]:
    """Curated RSS feeds + a rolling 7-day Google News search, as one group."""
    articles: list[Article] = []
    for feed in DEFAULT_FEEDS:
        articles.extend(fetch_feed(feed))
    articles.extend(fetch_google_news(GOOGLE_QUERY, when="7d", resolve=False))
    return [("recent", articles)]


def _fetch_backfill() -> list[Group]:
    """One group per weekly Google News window (newest-first) + NewsData.

    Keeping each weekly window as its own group lets the orchestrator cap them
    independently, so every week back to ``backfill_windows()``'s start
    contributes rather than the newest window consuming the whole budget.
    """
    groups: list[Group] = []
    for after, before in backfill_windows():
        articles = fetch_google_news(
            GOOGLE_QUERY, after=after, before=before, resolve=False
        )
        groups.append((f"backfill:{after}..{before}", articles))
    groups.append(("newsdata", fetch_newsdata(GOOGLE_QUERY)))
    return groups


def _gather(mode: str) -> list[Group]:
    if mode == "recent":
        return _fetch_recent()
    if mode == "backfill":
        return _fetch_backfill()
    if mode == "all":
        return _fetch_recent() + _fetch_backfill()
    raise ValueError(f"Unknown mode {mode!r}; expected recent|backfill|all.")


# --- orchestration -----------------------------------------------------------
class _RunState:
    """Mutable accumulator threaded through per-group processing."""

    def __init__(self, settings: Settings, skip_set: set[str], seen: set[str]):
        self.settings = settings
        self.skip_set = skip_set
        self.seen = seen
        self.written = 0
        self.skipped = 0
        self.filtered_out = 0
        self.enriched = 0
        self.examples: list[str] = []
        # Ids handled *this run* (in-memory) so duplicates across groups dedup
        # even before state is persisted.
        self.handled_this_run: set[str] = set()
        # Shared clients (created on first use; closed by the caller).
        self.resolver: object | None = None
        self.enricher: object | None = None


def _process_group(
    label: str,
    articles: list[Article],
    state: _RunState,
    *,
    group_limit: int | None,
    global_remaining: int | None,
) -> None:
    """Dedup/enrich/transform/write one labeled group, capped at ``group_limit``.

    ``global_remaining`` is the run-wide budget still available (``None`` =
    unlimited); the group stops at whichever of the two caps binds first.
    """
    written_here = 0
    for article in articles:
        if group_limit is not None and written_here >= group_limit:
            break
        if global_remaining is not None and written_here >= global_remaining:
            break

        assume_relevant = article.source_name in _WC_ONLY_SOURCES

        # Google News URLs arrive unresolved (opaque news.google.com links).
        # Resolving is the expensive step, so apply the cheap relevance filter
        # on title+body FIRST and only resolve entries we'd keep.
        if not assume_relevant and "news.google.com" in article.url:
            if not is_wc_relevant(f"{article.title}\n{article.body}"):
                state.filtered_out += 1
                continue
            if state.resolver is None:
                state.resolver = new_resolver_client()
            article.url = resolve_article_url(state.resolver, article.url)

        # Cheap pre-check: if the canonical id is already known, skip without
        # building a full RememberDoc.
        canonical = canonicalize_url(article.url)
        if not canonical:
            state.filtered_out += 1
            continue
        ext_id = external_id_for(canonical)
        if ext_id in state.skip_set or ext_id in state.handled_this_run:
            state.skipped += 1
            continue

        # The article survived relevance + dedup, so it is one we intend to
        # keep — only now is it worth a publisher-page fetch to recover the
        # full article text for thin (headline-only / summary) bodies.
        if state.enricher is None:
            state.enricher = new_enrich_client()
        if enrich_body(article, client=state.enricher):
            state.enriched += 1

        doc: RememberDoc | None = article_to_doc(
            article, assume_relevant=assume_relevant
        )
        if doc is None:
            state.filtered_out += 1
            continue

        # The doc's own external_id is authoritative (it canonicalizes too);
        # re-check against state in case the pre-check URL differed.
        if doc.external_id in state.skip_set or doc.external_id in state.handled_this_run:
            state.skipped += 1
            continue

        path = write_doc(state.settings, doc)
        state.handled_this_run.add(doc.external_id)
        state.seen.add(doc.external_id)
        state.written += 1
        written_here += 1
        if len(state.examples) < 5:
            state.examples.append(str(path))
    logger.info("Group %s: wrote %d docs", label, written_here)


def run(
    mode: str = "recent",
    limit: int | None = 60,
    *,
    per_window_limit: int | None = None,
    full: bool = False,
) -> dict:
    """Fetch, dedup, transform, and write new WC news docs.

    Returns a counts dict::

        {written, skipped, filtered_out, enriched, total_seen, examples}

    ``written`` new docs, ``skipped`` already-seen articles, ``filtered_out``
    non-WC / empty-body articles, ``enriched`` kept articles whose thin body was
    replaced with full publisher-page text. ``examples`` is a short list of
    written file paths for self-test / CLI display.

    Cost control uses two caps. ``limit`` (None = unlimited) is the run-wide
    ceiling on ``written``. ``per_window_limit`` (None = unlimited) caps each
    *group* independently — the recent group and every weekly backfill window —
    so a recent-news burst cannot starve the older windows. Use it for
    month-spanning backfills: e.g. ``per_window_limit=40`` lets every week from
    the backfill start contribute up to 40 docs regardless of how many recent
    articles arrived first.

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

    groups = _gather(mode)
    logger.info(
        "Mode %s gathered %d groups (%d raw articles).",
        mode,
        len(groups),
        sum(len(a) for _, a in groups),
    )

    state = _RunState(settings, skip_set, seen)
    try:
        for label, articles in groups:
            global_remaining = (
                None if limit is None else max(0, limit - state.written)
            )
            if global_remaining == 0:
                # Run-wide ceiling reached — leave the rest for a future run.
                break
            _process_group(
                label,
                articles,
                state,
                group_limit=per_window_limit,
                global_remaining=global_remaining,
            )
    finally:
        if state.resolver is not None:
            state.resolver.close()
        if state.enricher is not None:
            state.enricher.close()

    if seen != initial_seen:
        save_seen(settings, seen)

    counts = {
        "written": state.written,
        "skipped": state.skipped,
        "filtered_out": state.filtered_out,
        "enriched": state.enriched,
        "total_seen": len(seen),
        "examples": state.examples,
    }
    logger.info("News run complete: %s", {k: v for k, v in counts.items() if k != "examples"})
    return counts
