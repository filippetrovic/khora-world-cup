"""News fetch orchestrator: fetch -> dedup -> transform -> write.

Four modes:

* ``recent`` (default): the curated RSS set (FOX WC + BBC + ESPN + Sky +
  Guardian + the expanded reputable outlets) plus a rolling Google News
  ``when:7d`` search.
* ``backfill``: walk :func:`backfill_windows` newest-first, one Google News
  search per weekly window, back to ~May 11; plus a best-effort NewsData pull.
* ``gdelt``: the bulk source — a fan-out of GDELT DOC query variants across
  every day from ~May 11 to today (one group per query x day). Articles arrive
  body-less and are domain-filtered, deduped, then concurrently body-enriched.
* ``all``: ``gdelt`` then ``recent`` then ``backfill`` — the path that reaches
  ~2k unique WC-2026 docs after dedup + WC filter + domain filter.

Dedup happens *before* writing. Seen ``external_id`` values are persisted in
``data/state/news_seen.json``; an article already in that set is skipped
(never re-written, so the downstream remember worker never re-extracts it).
``limit`` caps how many *new* docs we write per run to control LLM cost.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

from khora_wc.config import Settings, get_settings
from khora_wc.contract import RememberDoc, write_doc
from khora_wc.ingest.news.enrich import enrich_bodies, enrich_body, new_enrich_client
from khora_wc.ingest.news.gdelt import (
    QUERY_VARIANTS as GDELT_QUERY_VARIANTS,
    daily_windows as gdelt_daily_windows,
    fetch_gdelt,
)
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
# Tailable file log so a long GDELT run is monitorable without relying on the
# buffered stdout pipe — see ``_setup_logging``.
LOG_FILENAME = "news_fetch.log"

# --- domain-quality filter ---------------------------------------------------
# GDELT indexes the whole web, including low-authority blogs and content farms.
# We skip these to keep the knowledge graph clean: a host whose registrable
# domain is in (or ends with) one of these is dropped before enrichment. Shared
# free-host suffixes catch the long tail of personal blogs; the explicit set
# catches known spam/aggregator farms.
_LOW_QUALITY_SUFFIXES: tuple[str, ...] = (
    "blogspot.com",
    "wordpress.com",
    "medium.com",
    "tumblr.com",
    "substack.com",
    "weebly.com",
    "wixsite.com",
    "blogger.com",
    "livejournal.com",
)
_LOW_QUALITY_DOMAINS: frozenset[str] = frozenset(
    {
        "newsbreak.com",
        "msn.com",  # syndication mirror — original publisher already indexed
        "news-galaxy.com",
        "sportskeeda.com",  # high-volume aggregator, thin pages
    }
)


def _is_quality_domain(url: str) -> bool:
    """False for low-authority / spam / aggregator hosts we want to skip.

    Matches the host against known free-blog suffixes and an explicit spam set;
    a ``www.`` prefix is ignored. Unparseable URLs are treated as low quality.
    """
    host = urlsplit(url).netloc.lower()
    if not host:
        return False
    if host.startswith("www."):
        host = host[4:]
    if host in _LOW_QUALITY_DOMAINS:
        return False
    return not any(
        host == suffix or host.endswith("." + suffix)
        for suffix in _LOW_QUALITY_SUFFIXES
    )


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


# --- file logging ------------------------------------------------------------
def _log_path(settings: Settings) -> Path:
    return settings.data_dir / "logs" / LOG_FILENAME


def _setup_logging(settings: Settings) -> Path:
    """Attach a tailable file-log sink for the whole news-fetch run.

    A long GDELT run's stdout is easily stuck behind a buffered pipe (e.g. a
    ``... | grep`` monitor), so progress, 429s, and throttle waits are also
    written to ``data/logs/news_fetch.log`` — appended, timestamped, and flushed
    *per record* so ``tail -f`` shows live progress.

    The handler is attached to the ``khora_wc.ingest.news`` package logger (whose
    level is forced to INFO) so every news submodule (``gdelt``, ``enrich``,
    ``google_news``, ...) lands in the same file — and *only* those, so unrelated
    modules are untouched. Records still propagate to the root console handler,
    which keeps its own level (``scripts/fetch_news.py`` sets WARNING / INFO from
    ``-v``), so console verbosity is unchanged: the file always gets INFO, the
    console only what its threshold allows. The sink is added at most once per
    process — repeated ``run()`` calls reuse it rather than double-logging.
    """
    path = _log_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)

    # The news package logger; attaching here (not root) keeps the file sink
    # scoped to news modules and leaves every other module's logging alone.
    news_logger = logging.getLogger("khora_wc.ingest.news")
    # Guard against double-add on repeated run() calls in one process: a sentinel
    # attribute on the handler lets us recognise our own sink.
    for handler in news_logger.handlers:
        if getattr(handler, "_news_fetch_sink", False):
            return path

    # A stdlib FileHandler flushes after every emitted record (StreamHandler.emit
    # calls flush()), so each line is on disk immediately for ``tail -f``.
    file_handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    file_handler._news_fetch_sink = True  # type: ignore[attr-defined]
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    news_logger.addHandler(file_handler)
    # Force the news package logger to INFO so its INFO progress lines reach the
    # file handler regardless of the root level basicConfig left behind. Records
    # still propagate to root's console handler (which applies its own level), so
    # the console stays exactly as verbose as ``-v`` made it — no quieter, no
    # noisier.
    news_logger.setLevel(logging.INFO)
    logger.info("News-fetch file log -> %s", path)
    return path


# --- fetch phases ------------------------------------------------------------
# Each phase is a *generator* that yields *labeled groups* ``(label, articles)``
# one at a time. A label is a string like ``"recent"`` or
# ``"backfill:2026-05-11..2026-05-18"``. The orchestrator applies its write cap
# *per group* so that a single recent-news burst cannot starve the older weekly
# backfill windows: every week from the backfill start gets its own budget and
# therefore contributes to the inbox.
#
# Crucially these are LAZY: the (throttled) GDELT request for a window only fires
# when ``run()`` pulls the next group, *after* the previous group's docs are on
# disk. That gives live monitorability (docs land per window) and lets a run-wide
# ``limit`` SHORT-CIRCUIT — once the budget is spent, ``run()`` stops iterating
# and no further GDELT requests are made.
#
# Google News URLs are resolved lazily in ``run()`` (see ``resolve=False``), so
# we only pay the per-link decode cost for entries we actually keep.
Group = tuple[str, list[Article]]


def _fetch_recent() -> Iterator[Group]:
    """Curated RSS feeds + a rolling 7-day Google News search, as one group."""
    articles: list[Article] = []
    for feed in DEFAULT_FEEDS:
        articles.extend(fetch_feed(feed))
    articles.extend(fetch_google_news(GOOGLE_QUERY, when="7d", resolve=False))
    yield ("recent", articles)


def _fetch_gdelt() -> Iterator[Group]:
    """One group per (query variant x daily window) GDELT search, yielded lazily.

    GDELT is the *bulk* source: each of :data:`GDELT_QUERY_VARIANTS` is searched
    across every day from ~May 11 to today, and each (query, day) pair becomes
    its own labeled group so the orchestrator's per-window cap applies to it
    independently. Articles arrive body-less; the orchestrator body-enriches the
    GDELT groups concurrently (see ``_process_group``). Empty windows are
    skipped so they don't clutter the stream.

    Groups are yielded *window-major* (all variants for the newest day, then the
    next day, ...) so that when a run-wide ``limit`` binds, the budget has been
    spread evenly across recent days rather than exhausted on a single query
    variant.

    Because this is a generator, each :func:`fetch_gdelt` (one throttled request)
    only fires when ``run()`` advances to the next group — i.e. *after* the prior
    window's docs are already written to disk. A run-wide ``limit`` therefore
    short-circuits the remaining requests simply by ``run()`` ceasing to iterate.
    """
    windows = gdelt_daily_windows()  # already newest-first
    for start, end in windows:
        for variant in GDELT_QUERY_VARIANTS:
            articles = fetch_gdelt(variant, start, end)
            if not articles:
                continue
            label = f"gdelt:{start}:{variant[:40]}"
            yield (label, articles)


def _fetch_backfill() -> Iterator[Group]:
    """One group per weekly Google News window (newest-first) + NewsData, lazily.

    Keeping each weekly window as its own group lets the orchestrator cap them
    independently, so every week back to ``backfill_windows()``'s start
    contributes rather than the newest window consuming the whole budget. Each
    window's Google News search fires only when ``run()`` pulls that group, so a
    run-wide ``limit`` short-circuits the remaining windows.
    """
    for after, before in backfill_windows():
        articles = fetch_google_news(
            GOOGLE_QUERY, after=after, before=before, resolve=False
        )
        yield (f"backfill:{after}..{before}", articles)
    yield ("newsdata", fetch_newsdata(GOOGLE_QUERY))


def _gather(mode: str) -> Iterator[Group]:
    """Lazily yield the labeled groups for ``mode`` (no upfront fetching).

    Chaining the phase generators keeps the whole pipeline lazy: in ``all`` mode
    the recent + backfill phases are not even started until the GDELT phase is
    exhausted (or the caller stops iterating because the run-wide limit bound).
    """
    if mode == "recent":
        yield from _fetch_recent()
    elif mode == "backfill":
        yield from _fetch_backfill()
    elif mode == "gdelt":
        yield from _fetch_gdelt()
    elif mode == "all":
        # GDELT first — it is the bulk source that carries the run toward ~2k;
        # recent + weekly Google backfill top it up with anything GDELT missed.
        yield from _fetch_gdelt()
        yield from _fetch_recent()
        yield from _fetch_backfill()
    else:
        raise ValueError(
            f"Unknown mode {mode!r}; expected recent|backfill|gdelt|all."
        )


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


def _effective_cap(group_limit: int | None, global_remaining: int | None) -> int | None:
    """Smaller of the two caps (``None`` = unlimited), for sizing a batch."""
    caps = [c for c in (group_limit, global_remaining) if c is not None]
    return min(caps) if caps else None


def _prepare_gdelt_articles(
    articles: list[Article],
    state: _RunState,
    *,
    cap: int | None,
) -> list[Article]:
    """Domain-filter + dedup GDELT articles, then async body-enrich the keepers.

    GDELT articles arrive body-less and in bulk, so we (1) drop low-authority
    domains, (2) drop articles whose canonical id is already seen — *before*
    paying for enrichment — then (3) concurrently fetch+extract bodies for at
    most ``cap`` survivors (a little headroom over the cap so enrich failures
    don't starve the write step). Returns the enriched candidates in order;
    ``_process_group`` still applies the hard write cap and relevance/body gates.
    """
    candidates: list[Article] = []
    # Take a little headroom over the cap so articles whose enrichment fails
    # (and thus get dropped at the body gate) don't leave the group short.
    headroom = None if cap is None else cap * 2
    for article in articles:
        if headroom is not None and len(candidates) >= headroom:
            break
        if not _is_quality_domain(article.url):
            state.filtered_out += 1
            continue
        canonical = canonicalize_url(article.url)
        if not canonical:
            state.filtered_out += 1
            continue
        ext_id = external_id_for(canonical)
        if ext_id in state.skip_set or ext_id in state.handled_this_run:
            state.skipped += 1
            continue
        candidates.append(article)

    if candidates:
        enriched = asyncio.run(enrich_bodies(candidates))
        state.enriched += enriched
    return candidates


def _process_gdelt_group(
    label: str,
    articles: list[Article],
    state: _RunState,
    *,
    group_limit: int | None,
    global_remaining: int | None,
) -> None:
    """Bulk GDELT path: domain-filter + dedup + async-enrich, then write.

    Mirrors ``_process_group``'s write/dedup accounting but uses the concurrent
    enrich (``enrich_bodies``) on the filtered survivors instead of a per-article
    sync fetch — the difference between minutes and hours for thousands of URLs.
    """
    cap = _effective_cap(group_limit, global_remaining)
    logger.info(
        "Group %s: %d raw articles fetched; filtering+dedup+enrich (cap=%s)",
        label,
        len(articles),
        cap,
    )
    candidates = _prepare_gdelt_articles(articles, state, cap=cap)

    written_here = 0
    for article in candidates:
        if group_limit is not None and written_here >= group_limit:
            break
        if global_remaining is not None and written_here >= global_remaining:
            break

        # GDELT is broad football news, so always run the WC relevance gate.
        doc: RememberDoc | None = article_to_doc(article, assume_relevant=False)
        if doc is None:
            state.filtered_out += 1
            continue
        if (
            doc.external_id in state.skip_set
            or doc.external_id in state.handled_this_run
        ):
            state.skipped += 1
            continue

        path = write_doc(state.settings, doc)
        state.handled_this_run.add(doc.external_id)
        state.seen.add(doc.external_id)
        state.written += 1
        written_here += 1
        if len(state.examples) < 5:
            state.examples.append(str(path))
    logger.info(
        "Group %s: wrote %d docs (running total: %d)",
        label,
        written_here,
        state.written,
    )


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
    unlimited); the group stops at whichever of the two caps binds first. GDELT
    groups (``gdelt:`` label prefix) take the bulk async-enrich path.
    """
    if label.startswith("gdelt:"):
        _process_gdelt_group(
            label,
            articles,
            state,
            group_limit=group_limit,
            global_remaining=global_remaining,
        )
        return

    logger.info("Group %s: %d raw articles fetched", label, len(articles))
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
    logger.info(
        "Group %s: wrote %d docs (running total: %d)",
        label,
        written_here,
        state.written,
    )


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
    log_path = _setup_logging(settings)
    logger.info(
        "=== news fetch: mode=%s limit=%s per_window=%s full=%s (log: %s) ===",
        mode, limit, per_window_limit, full, log_path,
    )
    seen = load_seen(settings)
    initial_seen = set(seen)
    # In ``full`` mode we ignore the persisted seen-set for the skip decision
    # but still record everything we write so the state is refreshed and in-run
    # cross-feed duplicates are collapsed via ``handled_this_run``.
    skip_set: set[str] = set() if full else seen

    logger.info("Mode %s: streaming groups (lazy fetch, incremental write).", mode)

    state = _RunState(settings, skip_set, seen)
    persisted_seen = set(initial_seen)
    try:
        # ``_gather`` is a generator: the (throttled) GDELT/Google request for the
        # next group fires only when we pull it via ``next()`` below, *after* the
        # previous group's docs are written — so the inbox grows steadily during
        # the run. We check the run-wide budget BEFORE advancing the generator so
        # that, once ``limit`` is hit, the *next* group is never even fetched (no
        # wasted throttled request). The rest is left for a future run.
        groups = _gather(mode)
        while True:
            global_remaining = (
                None if limit is None else max(0, limit - state.written)
            )
            if global_remaining == 0:
                logger.info(
                    "Run-wide limit %d reached; short-circuiting remaining groups "
                    "(no further fetches).",
                    limit,
                )
                break
            try:
                label, articles = next(groups)
            except StopIteration:
                break
            _process_group(
                label,
                articles,
                state,
                group_limit=per_window_limit,
                global_remaining=global_remaining,
            )
            # Checkpoint the seen-state after every group so an interrupt/crash
            # keeps the docs already written from being re-fetched on the next
            # run with the same args. Only write when something changed.
            if seen != persisted_seen:
                save_seen(settings, seen)
                persisted_seen = set(seen)
            logger.info("Running total written so far: %d", state.written)
    finally:
        if state.resolver is not None:
            state.resolver.close()
        if state.enricher is not None:
            state.enricher.close()
        # Final flush in case the last group(s) added ids after the loop's
        # per-group save (e.g. the ``break`` path) or the loop raised.
        if seen != persisted_seen:
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
