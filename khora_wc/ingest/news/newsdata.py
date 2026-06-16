"""NewsData.io fetcher (best-effort, unreliable).

Free tier: 200 credits/day, 10 articles/request, only a 48h reach (no
historical archive). Every failure mode — missing token, ratelimit, HTTP
error, malformed payload — is non-fatal: we log and return whatever we have so
the surrounding pipeline always continues.

The API key is sent in the ``X-ACCESS-KEY`` request header rather than the
``apikey`` query param so it never lands in a logged request URL.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from khora_wc.config import get_settings
from khora_wc.ingest.news.rss import Article, strip_html

logger = logging.getLogger(__name__)

LATEST_URL = "https://newsdata.io/api/1/latest"
SOURCE_NAME = "NewsData.io"
DEFAULT_QUERY = "FIFA World Cup 2026"
_TIMEOUT = 20.0

# NewsData pubDate looks like "2026-06-15 14:03:22" (UTC, space-separated).
_PUBDATE_FMT = "%Y-%m-%d %H:%M:%S"


def _parse_pubdate(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, _PUBDATE_FMT).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _result_to_article(result: dict) -> Article | None:
    title = (result.get("title") or "").strip()
    link = result.get("link") or ""
    if not title or not link:
        return None
    body = strip_html(result.get("content") or result.get("description") or "")
    return Article(
        title=title,
        body=body,
        url=link,
        published_at=_parse_pubdate(result.get("pubDate")),
        source_name=SOURCE_NAME,
        raw_id=result.get("article_id") or "",
    )


def fetch_newsdata(query: str = DEFAULT_QUERY, max_pages: int = 1) -> list[Article]:
    """Fetch recent articles from NewsData.io ``/latest``; never raises.

    Returns ``[]`` (and logs) on any error or ratelimit. Walks at most
    ``max_pages`` pages via the ``nextPage`` cursor to stay within the credit
    budget.
    """
    token = get_settings().newsdata_token
    if not token:
        logger.info("NewsData skipped: no NEWSDATA_TOKEN configured.")
        return []

    # Pass the key via header, never as a query param: httpx logs full request
    # URLs at INFO, and we must not leak the token value into logs.
    headers = {"X-ACCESS-KEY": token}
    params = {
        "q": query,
        "language": "en",
    }

    articles: list[Article] = []
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=headers) as client:
            page_token: str | None = None
            for page in range(max_pages):
                page_params = dict(params)
                if page_token:
                    page_params["page"] = page_token
                resp = client.get(LATEST_URL, params=page_params)
                if resp.status_code == 429:
                    logger.warning("NewsData ratelimited (429); stopping.")
                    break
                if resp.status_code >= 400:
                    # Log status only — never the response body / URL, which may
                    # echo the key or query. Non-fatal: return what we have.
                    logger.warning("NewsData HTTP %d; stopping.", resp.status_code)
                    break
                payload = resp.json()
                if payload.get("status") != "success":
                    logger.warning(
                        "NewsData non-success status: %s", payload.get("status")
                    )
                    break
                for result in payload.get("results") or []:
                    article = _result_to_article(result)
                    if article:
                        articles.append(article)
                page_token = payload.get("nextPage")
                if not page_token:
                    break
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("NewsData fetch failed (non-fatal): %s", exc)

    logger.info("NewsData %r: %d articles", query, len(articles))
    return articles
