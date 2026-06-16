"""Article -> RememberDoc transform, URL canonicalization, WC relevance filter.

An :class:`~khora_wc.ingest.news.rss.Article` becomes a ``RememberDoc`` only if
it is World-Cup-relevant (or came from a feed already known to be 100% WC) and
has a non-trivial body. The ``external_id`` is derived from the *canonical* URL
(utm_* params and fragments stripped) so the same story arriving via different
feeds / tracking links dedups to one id.
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from khora_wc.contract import RememberDoc
from khora_wc.ingest.news.rss import Article

# Minimum body length (chars) for a doc to be worth a downstream LLM extract.
MIN_BODY_CHARS = 60

# Case-insensitive World-Cup relevance terms matched against title + body.
WC_TERMS: tuple[str, ...] = (
    "world cup",
    "wc 2026",
    "wc2026",
    "fifa",
    "world cup 2026",
    "mundial",
    "knockout",
    "group stage",
    "round of 32",
    "round of 16",
    "quarter-final",
    "quarterfinal",
    "semi-final",
    "semifinal",
    "national team",
    "men's national",
)

# Query params we never want to keep when canonicalizing a URL.
# ``at_`` covers BBC's ``at_medium`` / ``at_campaign`` RSS attribution params.
_TRACKING_PREFIXES = ("utm_", "at_")
_TRACKING_KEYS = {
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "cmpid",
    "ito",
    "ocid",
}


def canonicalize_url(url: str) -> str:
    """Strip tracking params and fragments; normalize scheme/host casing.

    Keeps the path and any meaningful query params (e.g. article ids) so two
    genuinely different articles never collapse, but drops ``utm_*`` /
    ``fbclid`` / ``#fragment`` noise so the same article dedups cleanly.
    """
    split = urlsplit(url.strip())
    scheme = split.scheme.lower() or "https"
    netloc = split.netloc.lower()

    kept = [
        (k, v)
        for k, v in parse_qsl(split.query, keep_blank_values=False)
        if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_KEYS
    ]
    query = urlencode(kept)

    # Drop fragment entirely.
    return urlunsplit((scheme, netloc, split.path, query, ""))


def external_id_for(canonical_url: str) -> str:
    """``news:<sha1(canonical_url)>`` — the stable per-article id."""
    digest = hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()
    return f"news:{digest}"


def is_wc_relevant(text: str) -> bool:
    """True if ``text`` contains any World-Cup relevance term (case-insensitive)."""
    lowered = text.lower()
    return any(term in lowered for term in WC_TERMS)


def _normalize_letters(text: str) -> str:
    """Lowercase, keep only alphanumerics — for title/body containment checks."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _is_title_echo(title: str, body: str) -> bool:
    """True if the body is essentially a restatement of the title.

    Google News RSS bodies are just ``"<title> <publisher>"`` — they carry no
    information beyond the headline, so the body's alphanumerics are a subset of
    the title's. Such bodies make the content a useless triple-repeat, so the
    caller folds them down to a title-only doc.
    """
    body_letters = _normalize_letters(body)
    title_letters = _normalize_letters(title)
    if not body_letters:
        return True
    return body_letters in title_letters or title_letters in body_letters


def article_to_doc(a: Article, *, assume_relevant: bool = False) -> RememberDoc | None:
    """Convert an article to a ``RememberDoc``, or ``None`` if it should be dropped.

    Dropped when there is no title, or — unless ``assume_relevant`` (used for
    feeds already known to be 100% WC, e.g. FOX) — when neither title nor body
    matches a World-Cup relevance term. A body that merely restates the title
    (Google News headline-only entries) is folded away so the stored content is
    not a redundant triple-repeat; the headline + resolved URL + date are still
    worth remembering for date-scoped recall. A body with genuine prose must
    clear ``MIN_BODY_CHARS``.
    """
    body = (a.body or "").strip()
    title = (a.title or "").strip()
    if not title:
        return None

    title_only = _is_title_echo(title, body)
    if not title_only and len(body) < MIN_BODY_CHARS:
        return None

    if not assume_relevant and not is_wc_relevant(f"{title}\n{body}"):
        return None

    canonical = canonicalize_url(a.url)
    if not canonical:
        return None

    content = title if title_only else f"{title}\n\n{body}"
    return RememberDoc(
        external_id=external_id_for(canonical),
        content=content,
        title=title,
        source_type="news",
        source_name=a.source_name,
        source_url=canonical,
        source_timestamp=a.published_at,
    )
