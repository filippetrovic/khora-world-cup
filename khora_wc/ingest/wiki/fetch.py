"""Wikipedia fetch orchestrator: crawl -> transform -> dedup -> write.

Runs a bounded BFS crawl (:func:`~khora_wc.ingest.wiki.crawl.crawl`), turns each
page into a ``RememberDoc``, and writes the *new* ones into the inbox under
``data/inbox/wiki/``. Already-written articles are skipped via a persisted seen
set in ``data/state/wiki_seen.json`` (keyed by ``external_id``), so a re-run
never re-writes an article the remember worker has already extracted.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from khora_wc.config import Settings, get_settings
from khora_wc.contract import RememberDoc, write_doc
from khora_wc.ingest.wiki.crawl import SEED_TITLE, crawl
from khora_wc.ingest.wiki.transform import page_to_doc

logger = logging.getLogger(__name__)

STATE_FILENAME = "wiki_seen.json"


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


# --- orchestration -----------------------------------------------------------
def run(
    seed: str = SEED_TITLE,
    max_depth: int = 1,
    cap: int = 300,
    *,
    full: bool = False,
    all_links: bool = False,
) -> dict:
    """Crawl Wikipedia, transform, and write new docs into the inbox.

    Returns a counts dict::

        {written, skipped, filtered_out, total_seen, examples}

    ``written`` new docs, ``skipped`` already-seen articles, ``filtered_out``
    pages whose extract was too short/empty to keep. ``examples`` is a short list
    of written file paths for self-test / CLI display.

    ``full`` (default False) ignores the persisted ``wiki_seen.json`` so every
    crawled article is re-written (the seen-state is refreshed afterwards).

    ``all_links`` (default False) disables the WC-2026 relevance filter so the
    crawl follows *every* non-noise link (including the pre-2026 historical
    backlog). Default keeps the focused, WC-2026-only crawl.
    """
    settings = get_settings()
    seen = load_seen(settings)
    initial_seen = set(seen)
    skip_set: set[str] = set() if full else seen

    # Default keeps the WC-2026 relevance filter (crawl()'s own default, applied
    # when ``relevance`` is omitted); ``all_links`` passes None to crawl
    # everything.
    if all_links:
        pages = crawl(seed=seed, max_depth=max_depth, cap=cap, relevance=None)
    else:
        pages = crawl(seed=seed, max_depth=max_depth, cap=cap)
    logger.info("Crawl returned %d pages.", len(pages))

    written = 0
    skipped = 0
    filtered_out = 0
    examples: list[str] = []

    for page in pages:
        doc: RememberDoc | None = page_to_doc(page)
        if doc is None:
            filtered_out += 1
            continue
        if doc.external_id in skip_set:
            skipped += 1
            continue

        path = write_doc(settings, doc)
        seen.add(doc.external_id)
        written += 1
        if len(examples) < 5:
            examples.append(str(path))

    if seen != initial_seen:
        save_seen(settings, seen)

    counts = {
        "written": written,
        "skipped": skipped,
        "filtered_out": filtered_out,
        "total_seen": len(seen),
        "examples": examples,
    }
    logger.info(
        "Wiki run complete: %s", {k: v for k, v in counts.items() if k != "examples"}
    )
    return counts
