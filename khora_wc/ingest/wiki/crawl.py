"""Bounded breadth-first crawl of Wikipedia, seeded at the World Cup article.

Starting from "2026 FIFA World Cup", this walks outgoing *article-namespace*
links breadth-first. The crawl is deliberately bounded — an unbounded follow of
Wikipedia links is a follow of all of Wikipedia — by three knobs:

* ``max_depth`` — how many link-hops from the seed to follow. Depth 0 is the
  seed alone; depth 1 (default) is the seed plus the articles it links to.
* ``cap`` — a hard ceiling on the number of pages *fetched* (extracted), so the
  crawl stops once enough material is gathered regardless of depth.
* ``relevance`` — a ``(title) -> bool`` predicate; a title that fails it is
  neither fetched nor expanded. Defaults to :func:`is_wc_2026_relevant`, which
  drops dated-event noise — the pre-2026 historical-tournament backlog
  (1930..2022 World Cup pages) *and* unrelated 2026 events (other sports'
  championships/seasons reached via "2026 in sports" navigation) — so a moderate
  ``cap`` lands on *current* WC content (the 2026 tournament and its sub-pages,
  participating teams, host cities, stadiums, squads, and the players/coaches
  linked from those). Pass ``relevance=None`` to disable it and crawl every
  (non-noise) link. A light built-in noise filter (:func:`looks_like_noise`)
  always runs first regardless.

Dedup is by resolved title: the API resolves redirects, so two aliases for the
same article collapse to one fetch. The seed itself is always fetched even if it
would trip the noise/relevance filters.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from khora_wc.ingest.wiki.mediawiki import MediaWikiClient

logger = logging.getLogger(__name__)

SEED_TITLE = "2026 FIFA World Cup"

RelevanceFn = Callable[[str], bool]


@dataclass
class Page:
    """One fetched Wikipedia article: canonical title, URL, plain-text body."""

    title: str
    url: str
    text: str


def title_to_url(title: str) -> str:
    """Build the canonical article URL for a title (spaces -> underscores)."""
    return "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))


# Title prefixes/patterns that are almost never useful article *bodies* to
# ingest as prose. Kept light on purpose: at depth 1 from the WC page the vast
# majority of links are relevant (teams, cities, stadiums, players), so we only
# drop the obvious meta/navigation noise rather than risk over-filtering.
_NOISE_PREFIXES = (
    "list of ",
    "lists of ",
    "index of ",
    "outline of ",
    "timeline of ",
    "bibliography of ",
)

# A bare year ("2026") or year range ("2024–25") — calendar pages, not content.
_YEAR_ONLY_RE = re.compile(r"^\d{3,4}(?:[–-]\d{2,4})?$")


def looks_like_noise(title: str) -> bool:
    """True for titles we never want to fetch/expand (light heuristic).

    Drops "List of ..."/"Index of ..."/"Outline of ..." meta-pages,
    disambiguation pages, and bare year/year-range calendar pages. Everything
    else is allowed through — the WC page's direct links are overwhelmingly
    real content and we would rather over-include than miss a team or stadium.
    """
    lowered = title.lower()
    if any(lowered.startswith(prefix) for prefix in _NOISE_PREFIXES):
        return True
    if lowered.endswith("(disambiguation)"):
        return True
    if _YEAR_ONLY_RE.match(title.strip()):
        return True
    return False


# A title anchored to a specific year/season, e.g. "1930 FIFA World Cup",
# "2026 Formula One World Championship", "2025–26 USL Super League", "2026 in
# sports". The leading 4-digit year (optionally a season range like "2025–26")
# marks a *dated event* page, as opposed to a plain-name page (a team, city,
# stadium, or person), which never starts with a year.
_DATED_EVENT_RE = re.compile(r"^\d{4}(?:[–-]\d{2,4})?\b")

# Within dated-event pages, the ones we actually want: the 2026 FIFA World Cup
# itself and its sub-pages (groups, knockout stage, qualification, squads, draw,
# officials, ...). Requires the 2026 edition specifically.
_WC_2026_RE = re.compile(r"\b2026 FIFA World Cup\b", re.IGNORECASE)


def is_wc_2026_relevant(title: str) -> bool:
    """Default relevance predicate: keep WC-2026 content, drop dated noise.

    The 2026 World Cup seed page links to two big buckets of *dated event* pages
    that are not WC-2026 football content and would otherwise consume a moderate
    ``cap``:

    * the pre-2026 historical backlog — every "YYYY FIFA World Cup" plus its
      qualification / final / squads pages (~110), which sort alphabetically
      first; and
    * unrelated 2026 events pulled in from "2026 in sports" navigation —
      "2026 Formula One World Championship", "2026 Major League Baseball season",
      "2026 World Snooker Championship", "2026 in sports", etc. (~150).

    The rule: a title that *starts with a year* (a dated-event page) is kept only
    when it names the **2026 FIFA World Cup** specifically; every other dated
    page is dropped. Titles that do **not** start with a year are always kept —
    the genuinely relevant pages (national teams like "Argentina national
    football team", host cities like "Atlanta", stadiums like "MetLife Stadium",
    and individual players / coaches) carry plain names with no year, so this is
    a *denylist* of dated noise, never an allowlist that could drop them.
    """
    stripped = title.strip()
    if not _DATED_EVENT_RE.match(stripped):
        # Plain-name page (team / city / stadium / person) — always relevant.
        return True
    # Dated-event page: keep only the 2026 FIFA World Cup and its sub-pages.
    return bool(_WC_2026_RE.search(stripped))


# Sentinel so ``relevance=None`` can explicitly mean "no relevance filter"
# (crawl everything) while an *omitted* argument applies the WC-2026 default.
_DEFAULT_RELEVANCE = object()


def _keep(title: str, relevance: RelevanceFn | None) -> bool:
    """Whether ``title`` survives the noise filter and optional relevance fn."""
    if looks_like_noise(title):
        return False
    if relevance is not None and not relevance(title):
        return False
    return True


def crawl(
    seed: str = SEED_TITLE,
    max_depth: int = 1,
    cap: int = 300,
    relevance: RelevanceFn | None | object = _DEFAULT_RELEVANCE,
    *,
    client: MediaWikiClient | None = None,
) -> list[Page]:
    """Breadth-first crawl from ``seed``, returning the fetched :class:`Page`s.

    BFS by depth: the seed is depth 0; its ns0 links are depth 1; and so on up
    to ``max_depth``. A page is fetched (extract pulled) when first dequeued; its
    links are enqueued only while ``depth < max_depth``. Fetching stops as soon
    as ``cap`` pages have been collected. Dedup is by *resolved* title so redirect
    aliases never double-fetch.

    ``relevance`` and the built-in :func:`looks_like_noise` filter titles before
    they are fetched or expanded; the seed itself is exempt so a crawl always has
    a root. ``relevance`` defaults to :func:`is_wc_2026_relevant` (drops the
    pre-2026 historical backlog); pass ``relevance=None`` to disable it and crawl
    every non-noise link, or any ``(title) -> bool`` to customize.

    Pass an existing ``client`` to share a connection pool / delay config;
    otherwise a default :class:`MediaWikiClient` is created and closed here.
    """
    # Resolve the relevance sentinel: omitted -> WC-2026 default; explicit None
    # -> no filter; a callable -> use as given.
    if relevance is _DEFAULT_RELEVANCE:
        relevance = is_wc_2026_relevant
    relevance_fn: RelevanceFn | None = relevance  # type: ignore[assignment]

    owns_client = client is None
    client = client or MediaWikiClient()

    pages: list[Page] = []
    # Titles we've already enqueued/fetched (by the title as seen) plus the set
    # of resolved titles already turned into a Page, so a redirect alias that
    # resolves to an already-fetched article does not produce a duplicate.
    queued: set[str] = {seed}
    fetched_titles: set[str] = set()
    skipped = 0

    # Queue holds (title, depth). The seed bypasses the noise filter.
    queue: deque[tuple[str, int]] = deque([(seed, 0)])

    try:
        while queue:
            if len(pages) >= cap:
                logger.info("Reached cap=%d; stopping crawl.", cap)
                break

            title, depth = queue.popleft()

            result = client.get_extract(title)
            if result is None:
                skipped += 1
                logger.debug("No extract for %r; skipping.", title)
                continue

            resolved_title, text = result
            if resolved_title in fetched_titles:
                # A redirect alias of something we already have.
                continue
            fetched_titles.add(resolved_title)

            pages.append(
                Page(title=resolved_title, url=title_to_url(resolved_title), text=text)
            )
            logger.info(
                "[depth %d] fetched %r (%d chars) — %d/%d",
                depth,
                resolved_title,
                len(text),
                len(pages),
                cap,
            )

            # Expand only if we have depth budget AND room under the cap.
            if depth >= max_depth or len(pages) >= cap:
                continue

            for link in client.get_links(resolved_title):
                if link in queued or link in fetched_titles:
                    continue
                if not _keep(link, relevance_fn):
                    skipped += 1
                    continue
                queued.add(link)
                queue.append((link, depth + 1))
    finally:
        if owns_client:
            client.close()

    logger.info(
        "Crawl done: %d pages fetched, %d titles skipped (noise/relevance/no-extract).",
        len(pages),
        skipped,
    )
    return pages
