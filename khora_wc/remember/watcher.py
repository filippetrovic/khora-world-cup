"""Inbox -> khora ingest worker.

Reads ``RememberDoc`` JSON files from the inbox, remembers them through the
shared :class:`KhoraRuntime`, then moves each file into ``processed/`` (success)
or ``failed/`` (error), preserving the inbox-relative directory layout.

App-level dedup (khora gotcha A): re-remembering an existing ``external_id``
always triggers a full, costly re-extract -- it is NOT a cheap skip. So we track
a content checksum per ``external_id`` in ``data/state/ingested.json`` and skip
the remember entirely when the content is byte-identical to what we last
ingested.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import traceback
from pathlib import Path

from khora_wc.config import Settings, get_settings
from khora_wc.contract import iter_inbox, read_doc
from khora_wc.runtime import KhoraRuntime

logger = logging.getLogger(__name__)


def _state_path(settings: Settings) -> Path:
    return settings.state_dir / "ingested.json"


def _load_state(settings: Settings) -> dict[str, str]:
    """Load the ``external_id -> sha256(content)`` map (empty if absent/bad)."""
    path = _state_path(settings)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("could not read ingest state %s (%s); starting empty", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(settings: Settings, state: dict[str, str]) -> None:
    """Atomically persist the ingest state map."""
    path = _state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _relative_to_inbox(settings: Settings, path: Path) -> Path:
    """Return ``path`` relative to the inbox, falling back to its filename."""
    try:
        return path.relative_to(settings.inbox_dir)
    except ValueError:
        return Path(path.name)


def _move_into(settings: Settings, src: Path, dest_root: Path) -> Path:
    """Move ``src`` into ``dest_root`` preserving its inbox-relative subpath."""
    rel = _relative_to_inbox(settings, src)
    dest = dest_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    # ``shutil.move`` handles cross-device moves and overwrites a stale dest.
    if dest.exists():
        dest.unlink()
    shutil.move(str(src), str(dest))
    return dest


def _iter_processed(settings: Settings) -> list[Path]:
    """Return all ``*.json`` docs already moved to ``processed/`` (sorted).

    These are docs that were previously ingested and live on disk as the source
    of truth; ``--reingest`` re-reads them so a from-state re-ingest covers the
    whole corpus, not just whatever is currently waiting in the inbox.
    """
    processed = settings.processed_dir
    if not processed.exists():
        return []
    return sorted(processed.rglob("*.json"))


async def process_inbox_once(runtime: KhoraRuntime, *, reingest: bool = False) -> dict:
    """Process every inbox file once; return ``{ingested, skipped, failed}``.

    With ``reingest=True`` the ``ingested.json`` content-hash dedup is bypassed
    so every doc is re-``remember``ed (khora's external_id upsert replaces the
    prior version). Re-ingest also covers docs already in ``processed/`` —
    those are re-read in place and NOT moved, since processed/ is the on-disk
    source of truth.
    """
    settings = get_settings()
    state = _load_state(settings)

    ingested = skipped = failed = 0

    def _fail(path: Path, *, move: bool) -> None:
        """Record a failure; optionally move the offending file into failed/."""
        nonlocal failed
        failed += 1
        tb = traceback.format_exc()
        logger.error("failed to ingest %s:\n%s", path, tb)
        if not move:
            # processed/ docs stay put on failure (they are the source of
            # truth); we still surface the error in the log above.
            return
        try:
            dest = _move_into(settings, path, settings.failed_dir)
            dest.with_name(dest.name + ".error.txt").write_text(tb, encoding="utf-8")
        except Exception:  # noqa: BLE001 - never let cleanup mask the batch
            logger.exception("could not move failed file %s into failed/", path)

    # --- inbox docs: ingest then move into processed/ -----------------------
    for path in iter_inbox(settings):
        try:
            doc = read_doc(path)
            content_hash = _content_sha256(doc.content)

            if not reingest and state.get(doc.external_id) == content_hash:
                # Cheap skip: content is byte-identical to the last ingest, so
                # we avoid the costly re-extract entirely (gotcha A).
                _move_into(settings, path, settings.processed_dir)
                skipped += 1
                logger.debug("skip (unchanged) %s -> processed", doc.external_id)
                continue

            await runtime.remember(doc)
            state[doc.external_id] = content_hash
            _save_state(settings, state)
            _move_into(settings, path, settings.processed_dir)
            ingested += 1
            logger.debug("ingested %s -> processed", doc.external_id)

        except Exception:  # noqa: BLE001 - one bad file must not abort the batch
            _fail(path, move=True)

    # --- processed docs: only revisited on an explicit re-ingest ------------
    # Re-read each previously-processed doc and remember it again IN PLACE (no
    # move), so a re-ingest replays the entire corpus through khora.
    if reingest:
        for path in _iter_processed(settings):
            try:
                doc = read_doc(path)
                content_hash = _content_sha256(doc.content)
                await runtime.remember(doc)
                state[doc.external_id] = content_hash
                _save_state(settings, state)
                ingested += 1
                logger.debug("re-ingested %s (in place, processed/)", doc.external_id)
            except Exception:  # noqa: BLE001 - one bad file must not abort the batch
                _fail(path, move=False)

    return {"ingested": ingested, "skipped": skipped, "failed": failed}


async def watch_inbox(
    runtime: KhoraRuntime,
    interval: float = 5.0,
    stop_event: asyncio.Event | None = None,
    *,
    reingest: bool = False,
) -> None:
    """Poll the inbox every ``interval`` seconds until ``stop_event`` is set.

    ``reingest`` (if set) applies only to the FIRST pass — it replays the whole
    on-disk corpus once; subsequent polls run the normal skip-if-unchanged path
    so the loop doesn't re-extract everything every cycle.
    """
    stop_event = stop_event or asyncio.Event()
    logger.info("watch_inbox started (interval=%.1fs, reingest=%s)", interval, reingest)
    first = True
    while not stop_event.is_set():
        counts = await process_inbox_once(runtime, reingest=reingest and first)
        first = False
        if any(counts.values()):
            logger.info(
                "inbox cycle: ingested=%(ingested)d skipped=%(skipped)d "
                "failed=%(failed)d",
                counts,
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    logger.info("watch_inbox stopped")
