"""RSS news fetchers.

Parses a small set of confirmed-reachable football/World-Cup RSS feeds into a
common :class:`Article` dataclass. HTML is stripped from bodies with the stdlib
``html.parser`` (no bs4 dependency). The FOX Sports WC feed is the richest
source — it carries the full article body in ``content:encoded`` (exposed by
feedparser as ``entry.content[0].value``); other feeds only give a summary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from time import struct_time

import feedparser

logger = logging.getLogger(__name__)

# A browser-ish UA — some CDNs (BBC, Guardian) 403 the default feedparser UA.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class Article:
    """A single fetched news article in source-agnostic form.

    ``url`` is the publisher URL as it arrived from the feed (Google News links
    are still redirect URLs at this point — see ``google_news`` for resolution).
    ``raw_id`` is the feed-native id (guid / entry id) when present, else "".
    """

    title: str
    body: str
    url: str
    published_at: datetime | None
    source_name: str
    raw_id: str = ""


# --- feed registry -----------------------------------------------------------
# Each entry: (source_name, url). ``wc_only`` feeds skip the relevance filter
# downstream (their content is already 100% World Cup).
@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    wc_only: bool = False


_FOX_WC = (
    "https://api.foxsports.com/v2/content/optimized-rss"
    "?partnerKey=MB0Wehpmuj2lUhuRhQaafhBjAJqaPU244mlTDK1i&size=30&tags=soccer/wc/league/12"
)

DEFAULT_FEEDS: tuple[Feed, ...] = (
    Feed("FOX Sports", _FOX_WC, wc_only=True),
    Feed("BBC Sport", "https://feeds.bbci.co.uk/sport/football/rss.xml"),
    Feed("ESPN", "https://www.espn.com/espn/rss/soccer/news"),
    Feed("Sky Sports", "https://www.skysports.com/rss/12040"),
    Feed("The Guardian", "https://www.theguardian.com/football/rss"),
    # --- expanded reputable football outlets ---------------------------------
    # All non-wc_only unless noted: they cover football broadly, so each article
    # still runs through the World-Cup relevance filter (transform.is_wc_relevant)
    # before it is written. Names are stable provenance labels for the graph.
    # Every URL below was verified to return items at build time (2026-06-16);
    # the handful of outlets without a working public RSS feed (Goal, Bleacher
    # Report, SI, The Athletic — all JS-gated or deprecated) are intentionally
    # omitted rather than left as dead entries.
    Feed("Guardian World Cup 2026",
         "https://www.theguardian.com/football/world-cup-2026/rss", wc_only=True),
    Feed("CBS Sports", "https://www.cbssports.com/rss/headlines/soccer/"),
    Feed("Yahoo Sports", "https://sports.yahoo.com/soccer/rss/"),
    Feed("Inside World Football", "https://www.insideworldfootball.com/feed/"),
    Feed("101 Great Goals", "https://www.101greatgoals.com/feed/"),
    Feed("World Soccer Talk", "https://worldsoccertalk.com/feed/"),
    Feed("FourFourTwo", "https://www.fourfourtwo.com/feeds/all"),
    Feed("SB Nation Soccer", "https://www.sbnation.com/rss/soccer/index.xml"),
    Feed("NYT Soccer", "https://rss.nytimes.com/services/xml/rss/nyt/Soccer.xml"),
    Feed("Football Italia", "https://football-italia.net/feed/"),
    Feed("Get Football News France", "https://www.getfootballnewsfrance.com/feed/"),
    # --- per-nation outlets (non-English bodies still pass the WC filter on
    # their WC/Mundial terms; provenance labels keep the source nation clear) ---
    Feed("L'Equipe", "https://dwh.lequipe.fr/api/edito/rss?path=/Football/"),
    Feed("Marca", "https://e00-marca.uecdn.es/rss/futbol/mundial.xml"),
    Feed("AS", "https://as.com/rss/futbol/mundial.xml"),
    Feed("Mundo Deportivo", "https://www.mundodeportivo.com/rss/futbol/mundial.xml"),
    Feed("Globo Esporte", "https://ge.globo.com/rss/ge/futebol/copa-do-mundo/"),
)


# --- HTML stripping ----------------------------------------------------------
class _TextExtractor(HTMLParser):
    """Collect visible text from an HTML fragment, dropping script/style."""

    _SKIP = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skipping = False

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self._SKIP:
            self._skipping = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skipping = False

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def strip_html(raw: str | None) -> str:
    """Return the visible text of an HTML fragment with whitespace collapsed."""
    if not raw:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        text = parser.text()
    except Exception:  # malformed markup — fall back to the raw string
        text = raw
    return " ".join(text.split())


# --- field extraction --------------------------------------------------------
def _parsed_to_datetime(parsed: struct_time | None) -> datetime | None:
    """Convert a feedparser ``*_parsed`` struct_time to an aware UTC datetime."""
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _entry_body(entry: feedparser.FeedParserDict) -> str:
    """Best available body text for an entry.

    Prefers ``content:encoded`` (full article, e.g. FOX), then ``summary`` /
    ``description``. Always HTML-stripped.
    """
    content = entry.get("content")
    if content:
        value = content[0].get("value") if isinstance(content, list) else None
        stripped = strip_html(value)
        if stripped:
            return stripped
    return strip_html(entry.get("summary") or entry.get("description") or "")


def _entry_published(entry: feedparser.FeedParserDict) -> datetime | None:
    return _parsed_to_datetime(
        entry.get("published_parsed") or entry.get("updated_parsed")
    )


def _entry_raw_id(entry: feedparser.FeedParserDict) -> str:
    return entry.get("id") or entry.get("guid") or ""


def parse_entries(
    parsed: feedparser.FeedParserDict, source_name: str
) -> list[Article]:
    """Map every entry of a parsed feed into an :class:`Article`."""
    articles: list[Article] = []
    for entry in parsed.entries:
        link = entry.get("link") or ""
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue
        articles.append(
            Article(
                title=title,
                body=_entry_body(entry),
                url=link,
                published_at=_entry_published(entry),
                source_name=source_name,
                raw_id=_entry_raw_id(entry),
            )
        )
    return articles


def fetch_feed(feed: Feed) -> list[Article]:
    """Fetch and parse a single feed; never raises (logs and returns [])."""
    try:
        parsed = feedparser.parse(feed.url, agent=USER_AGENT)
    except Exception as exc:  # network/parse blow-up — non-fatal
        logger.warning("Failed to fetch feed %s: %s", feed.name, exc)
        return []

    # feedparser sets ``bozo`` for malformed/partial feeds but often still
    # yields usable entries, so only treat it as failure when nothing parsed.
    if not parsed.entries:
        if getattr(parsed, "bozo", 0):
            logger.warning(
                "Feed %s returned no entries (bozo: %s)",
                feed.name,
                getattr(parsed, "bozo_exception", "unknown"),
            )
        else:
            logger.info("Feed %s returned no entries.", feed.name)
        return []

    articles = parse_entries(parsed, feed.name)
    logger.info("Feed %s: %d articles", feed.name, len(articles))
    return articles


def fetch_rss(feeds: tuple[Feed, ...] = DEFAULT_FEEDS) -> list[Article]:
    """Fetch every feed in ``feeds`` and return the concatenated articles."""
    articles: list[Article] = []
    for feed in feeds:
        articles.extend(fetch_feed(feed))
    return articles
