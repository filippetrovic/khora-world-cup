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
from collections.abc import Callable
from pathlib import Path

from khora_wc.config import Settings, get_settings
from khora_wc.contract import RememberDoc, iter_inbox, read_doc
from khora_wc.runtime import _BATCH_MAX_CONCURRENT, KhoraRuntime

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


async def process_inbox_once(
    runtime: KhoraRuntime,
    *,
    reingest: bool = False,
    batch_size: int = 50,
    max_concurrent: int = _BATCH_MAX_CONCURRENT,
) -> dict:
    """Process every inbox file once; return ``{ingested, skipped, failed}``.

    Docs are ingested in chunks of ``batch_size`` through
    :meth:`KhoraRuntime.remember_batch`, which extracts up to ``max_concurrent``
    docs in parallel (~3-5x faster than the old one-at-a-time loop) while keeping
    the embedded single-writer store safe under one lock. ``max_concurrent`` is
    the knob to benchmark (10/20/50): it parallelizes the LLM extraction, but the
    SQLite writes still serialize, so higher is not always faster.

    The ``ingested.json`` content-hash dedup runs FIRST (gotcha A): an unchanged
    doc is moved to ``processed/`` without any remember. Each remembered file is
    then moved to ``processed/`` (success) or ``failed/`` (error) per its own
    batch result, so one bad doc never strands the rest of its chunk.

    With ``reingest=True`` the dedup is bypassed so every doc is re-``remember``ed
    (khora's external_id upsert replaces the prior version inside the batch).
    Re-ingest also covers docs already in ``processed/`` — those are re-read in
    place and NOT moved, since processed/ is the on-disk source of truth.
    """
    settings = get_settings()
    state = _load_state(settings)

    counts = {"ingested": 0, "skipped": 0, "failed": 0}

    def _fail(path: Path, *, move: bool, error: str | None = None) -> None:
        """Record a failure; optionally move the offending file into failed/."""
        counts["failed"] += 1
        tb = error if error is not None else traceback.format_exc()
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

    # --- inbox docs: dedup, then ingest in batches and move into processed/ --
    # A pending entry pairs the on-disk path with its parsed doc + content hash
    # so post-batch we can move the file and persist the hash by external_id.
    pending: list[tuple[Path, "RememberDoc", str]] = []
    for path in iter_inbox(settings):
        try:
            doc = read_doc(path)
            content_hash = _content_sha256(doc.content)
        except Exception:  # noqa: BLE001 - a bad file must not abort the batch
            _fail(path, move=True)
            continue

        if not reingest and state.get(doc.external_id) == content_hash:
            # Cheap skip: content is byte-identical to the last ingest, so we
            # avoid the costly re-extract entirely (gotcha A).
            try:
                _move_into(settings, path, settings.processed_dir)
            except Exception:  # noqa: BLE001 - isolate a move failure
                _fail(path, move=True)
                continue
            counts["skipped"] += 1
            logger.debug("skip (unchanged) %s -> processed", doc.external_id)
            continue

        pending.append((path, doc, content_hash))

    for chunk in _chunked(pending, batch_size):
        await _ingest_chunk(
            runtime, settings, state, chunk, counts, _fail, max_concurrent=max_concurrent
        )

    # --- processed docs: only revisited on an explicit re-ingest ------------
    # Re-read each previously-processed doc and remember it again IN PLACE (no
    # move), so a re-ingest replays the entire corpus through khora.
    if reingest:
        reprocess: list[tuple[Path, "RememberDoc", str]] = []
        for path in _iter_processed(settings):
            try:
                doc = read_doc(path)
                content_hash = _content_sha256(doc.content)
            except Exception:  # noqa: BLE001 - one bad file must not abort
                _fail(path, move=False)
                continue
            reprocess.append((path, doc, content_hash))

        for chunk in _chunked(reprocess, batch_size):
            await _ingest_chunk(
                runtime,
                settings,
                state,
                chunk,
                counts,
                _fail,
                max_concurrent=max_concurrent,
                move=False,
            )

    return counts


def _chunked(items: list, size: int) -> list[list]:
    """Split ``items`` into consecutive chunks of at most ``size`` (>=1)."""
    size = max(1, size)
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _ingest_chunk(
    runtime: KhoraRuntime,
    settings: Settings,
    state: dict[str, str],
    chunk: list[tuple[Path, "RememberDoc", str]],
    counts: dict[str, int],
    fail: "Callable[..., None]",
    *,
    max_concurrent: int,
    move: bool = True,
) -> None:
    """Batch-ingest one chunk, then act on each doc's per-doc result.

    ``max_concurrent`` is the in-flight extraction parallelism handed to
    :meth:`KhoraRuntime.remember_batch`. Per-doc results are acted on
    individually — a partial-batch failure still moves the succeeded docs to
    ``processed/`` and only the failures to ``failed/``. ``move`` is False on the
    re-ingest of ``processed/`` docs (they stay in place as the source of truth).
    On a hard batch failure (an unexpected exception escaping
    :meth:`remember_batch`, which its reopen+serial fallback makes unlikely)
    every doc in the chunk is failed individually so one bad chunk never aborts
    the whole pass.
    """
    docs = [doc for _, doc, _ in chunk]
    by_ext: dict[str, tuple[Path, str]] = {
        doc.external_id: (path, content_hash) for path, doc, content_hash in chunk
    }
    try:
        batch_results = await runtime.remember_batch(docs, max_concurrent=max_concurrent)
    except Exception:  # noqa: BLE001 - whole chunk failed; isolate per file
        for path, _doc, _hash in chunk:
            fail(path, move=move)
        return

    for res in batch_results:
        path, content_hash = by_ext[res.external_id]
        if not res.success:
            fail(path, move=move, error=res.error or "batch ingest failed")
            continue
        state[res.external_id] = content_hash
        _save_state(settings, state)
        counts["ingested"] += 1
        if move:
            try:
                _move_into(settings, path, settings.processed_dir)
            except Exception:  # noqa: BLE001 - ingest succeeded; flag move issue
                logger.exception("ingested %s but could not move into processed/", path)
        logger.debug("ingested %s%s", res.external_id, "" if move else " (in place)")


async def watch_inbox(
    runtime: KhoraRuntime,
    interval: float = 5.0,
    stop_event: asyncio.Event | None = None,
    *,
    reingest: bool = False,
    max_concurrent: int = _BATCH_MAX_CONCURRENT,
) -> None:
    """Poll the inbox every ``interval`` seconds until ``stop_event`` is set.

    ``reingest`` (if set) applies only to the FIRST pass — it replays the whole
    on-disk corpus once; subsequent polls run the normal skip-if-unchanged path
    so the loop doesn't re-extract everything every cycle. ``max_concurrent`` is
    forwarded to every :func:`process_inbox_once` pass.
    """
    stop_event = stop_event or asyncio.Event()
    logger.info(
        "watch_inbox started (interval=%.1fs, reingest=%s, max_concurrent=%d)",
        interval,
        reingest,
        max_concurrent,
    )
    first = True
    while not stop_event.is_set():
        counts = await process_inbox_once(
            runtime, reingest=reingest and first, max_concurrent=max_concurrent
        )
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
