"""Pure transform: a crawled :class:`Page` -> :class:`RememberDoc`.

Wikipedia content is encyclopedic, not time-anchored, so ``source_timestamp`` is
left ``None`` (recall should not date-scope it the way it does news). The
``external_id`` is ``wiki:<title-slug>`` so re-crawling the same article dedups
to one stored doc. Very short or empty extracts (stubs, redirects that slipped
through) are dropped — they are not worth a downstream embed/extract.
"""

from __future__ import annotations

import re

from khora_wc.contract import RememberDoc
from khora_wc.ingest.wiki.crawl import Page

SOURCE_TYPE = "wiki"
SOURCE_NAME = "Wikipedia"

# Minimum extract length (chars) for an article to be worth storing. Below this
# the page is a stub / redirect remnant with no real prose.
MIN_EXTRACT_CHARS = 200

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def title_slug(title: str) -> str:
    """Slugify an article title for the external id.

    e.g. "MetLife Stadium" -> "metlife-stadium". Empty results fall back to
    ``"page"`` so we never emit ``wiki:``.
    """
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    return slug or "page"


def page_to_doc(page: Page) -> RememberDoc | None:
    """Turn a crawled :class:`Page` into a ``RememberDoc``, or ``None`` to drop.

    Dropped when the extract is empty or shorter than :data:`MIN_EXTRACT_CHARS`.
    The stored ``content`` leads with the title (so the article subject is part
    of the embedded text), then the full plain-text body.
    """
    text = (page.text or "").strip()
    if len(text) < MIN_EXTRACT_CHARS:
        return None

    title = (page.title or "").strip()
    content = f"{title}\n\n{text}" if title else text

    return RememberDoc(
        external_id=f"wiki:{title_slug(title)}",
        content=content,
        title=title,
        source_type=SOURCE_TYPE,
        source_name=SOURCE_NAME,
        source_url=page.url,
        source_timestamp=None,
    )
