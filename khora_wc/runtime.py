"""Single-session khora runtime shared by the watcher and the read API.

The embedded ``sqlite_lance`` backend is a single-writer store, so EVERY khora
call in the process is serialized through one :class:`asyncio.Lock`. The runtime
also owns the documented recovery for the known #884 write-lock bug: a remember
on the replace path can hit a SQLite ``database is locked`` error (and/or report
a ``graph_mirror_failed`` degradation) that poisons the session handle so every
subsequent write fails. The only reliable in-app recovery is to close and reopen
the session; the persistent namespace UUID survives, so a reopen is seamless.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from khora import Khora, RecallResult, RememberResult, Stats

from khora_wc.contract import RememberDoc
from khora_wc.expertise import ENTITY_TYPES, RELATIONSHIP_TYPES, WC_EXPERTISE
from khora_wc.khora_client import open_khora, recall, remember_doc

logger = logging.getLogger(__name__)

# Substring (case-insensitive) that marks the #884 embedded SQLite write-lock
# error. Once seen, the session handle is poisoned and must be reopened.
_LOCK_MARKER = "database is locked"

# Default in-flight concurrency for batch ingestion. khora's batched pipeline
# runs extraction for this many docs concurrently (the real speedup) while
# serializing the actual SQLite writes internally; the embedded single-writer
# store stays safe because the whole batch still runs under the runtime's one
# lock as a single operation. Tunable per call (and via the watcher CLI) so the
# write-contention vs. extraction-parallelism trade-off can be benchmarked at
# 10/20/50 — higher values raise the chance of surfacing the #884 write-lock
# degradation, which the reopen+serial fallback below absorbs.
_BATCH_MAX_CONCURRENT = 10


@dataclass(slots=True)
class BatchDocResult:
    """Per-document outcome of a :meth:`KhoraRuntime.remember_batch` call.

    The watcher keys these by ``external_id`` to decide where each inbox file
    goes: ``success`` -> ``processed/``, otherwise ``failed/`` (with ``error``).
    ``doc_id`` is the khora document UUID (string) when known.
    """

    external_id: str
    success: bool
    doc_id: str | None = None
    error: str | None = None


def _is_lock_error(exc: BaseException) -> bool:
    """True when an exception looks like the #884 write-lock error."""
    return _LOCK_MARKER in str(exc).lower()


def doc_to_batch_dict(doc: RememberDoc) -> dict:
    """Map a :class:`RememberDoc` to the khora ``remember_batch`` document dict.

    khora's batch APIs take a list of plain dicts (not the kwargs that single
    :func:`remember_doc` uses). Per-doc keys here override the batch-level
    ``source_type``/``source_name``/``source_url``/``source_timestamp`` kwargs,
    so we stamp every field explicitly to keep ingest identical to the serial
    path. ``external_id`` drives khora's replace-on-existing upsert inside the
    batch (Stage 0a dispatches matching ids to the single-doc replace path).
    """
    return {
        "content": doc.content,
        "title": doc.title,
        "external_id": doc.external_id,
        "source_type": doc.source_type,
        # Single-doc path passes ``source_name or None``; mirror that so an
        # empty string never reaches the store as a literal "".
        "source_name": doc.source_name or None,
        "source_url": doc.source_url,
        "source_timestamp": doc.source_timestamp,
        "metadata": doc.metadata,
    }


def _result_is_poisoned(result: RememberResult | object) -> bool:
    """True when a result reports a #884 poisoning degradation.

    On the replace path khora can mirror the graph-store failure as a
    ``graph_mirror_failed`` degradation under ``metadata["degradations"]`` rather
    than raising. Either form leaves the write lock held, so we treat it the same
    as a raised lock error and reopen before the next write. Accepts both
    :class:`RememberResult` and :class:`BatchResult` (both carry ``metadata``);
    the batch path inherits per-doc degradations into its aggregate metadata.
    """
    metadata = getattr(result, "metadata", None) or {}
    degradations = metadata.get("degradations") or []
    for deg in degradations:
        # Degradations are small mappings/objects; stringifying is cheap and
        # robust to shape changes across khora versions.
        text = str(deg).lower()
        if "graph_mirror_failed" in text or _LOCK_MARKER in text:
            return True
    return False


class KhoraRuntime:
    """Owns one connected khora session and serializes access to it."""

    def __init__(self, cm, kb: Khora, namespace_id: UUID, lock: asyncio.Lock) -> None:
        # Constructed via :meth:`create`; ``cm`` is the live open_khora context
        # manager we drive manually so we can reopen it on lock recovery.
        self._cm = cm
        self.kb = kb
        self.namespace_id = namespace_id
        self._lock = lock

    @classmethod
    async def create(cls) -> "KhoraRuntime":
        """Open the khora session and return a ready runtime."""
        cm = open_khora()
        kb, namespace_id = await cm.__aenter__()
        logger.info("KhoraRuntime opened (namespace=%s)", namespace_id)
        return cls(cm, kb, namespace_id, asyncio.Lock())

    async def remember(self, doc: RememberDoc) -> RememberResult:
        """Remember a doc, recovering once from the #884 write-lock bug.

        Holds the lock for the whole call (including any reopen+retry) so no
        other coroutine can touch the poisoned/just-reopened handle in between.
        """
        async with self._lock:
            return await self._remember_locked(doc)

    async def _remember_locked(self, doc: RememberDoc) -> RememberResult:
        """Single-doc remember with #884 recovery; caller must hold ``self._lock``.

        Split out of :meth:`remember` so the batch serial-fallback path can reuse
        the exact same recovery logic without re-acquiring the (non-reentrant)
        lock it already holds.
        """
        try:
            result = await remember_doc(self.kb, self.namespace_id, doc)
        except Exception as exc:  # noqa: BLE001 - inspect, recover, re-raise
            if not _is_lock_error(exc):
                raise
            logger.warning(
                "remember(%s) hit write-lock error; reopening session and "
                "retrying once: %s",
                doc.external_id,
                exc,
            )
            await self._reopen()
            return await remember_doc(self.kb, self.namespace_id, doc)

        # Succeeded, but the result may report a poisoning degradation that
        # leaves the lock held for the NEXT write. Reopen now and retry so we
        # return a clean (non-degraded) result and a healthy session.
        if _result_is_poisoned(result):
            logger.warning(
                "remember(%s) reported a graph-mirror degradation; reopening "
                "session and retrying once",
                doc.external_id,
            )
            await self._reopen()
            return await remember_doc(self.kb, self.namespace_id, doc)
        return result

    async def remember_batch(
        self, docs: list[RememberDoc], *, max_concurrent: int = _BATCH_MAX_CONCURRENT
    ) -> list[BatchDocResult]:
        """Remember many docs concurrently; return one result per doc.

        Runs khora's batched pipeline (extraction concurrent up to
        ``max_concurrent`` in-flight, SQLite writes serialized internally) as a
        SINGLE operation under the runtime's one lock — so the embedded
        single-writer store is never touched by two coroutines at once, exactly
        as on the serial path.

        Per-doc results: ``remember_batch`` returns only aggregate counts
        (:class:`khora.BatchResult` — no per-doc detail), so we derive each
        doc's outcome from a post-batch status lookup by ``external_id``
        (COMPLETED -> success). That lookup resolves the namespace to the active
        version row id first (see :meth:`_collect_statuses`), which is mandatory
        on Postgres. ``external_id`` upsert/replace applies inside the batch
        (Stage 0a routes existing ids to the replace path), so re-ingesting an
        existing doc replaces it just like :meth:`remember`.

        #884 recovery: if the batch raises/returns a write-lock degradation we
        reopen the session and retry the *remaining* (not-yet-COMPLETED) docs
        once; if that still fails we fall back to serial :meth:`_remember_locked`
        for the leftover docs so one poisoned batch can't strand a whole chunk.
        Results preserve input order.
        """
        if not docs:
            return []

        async with self._lock:
            return await self._remember_batch_locked(docs, max_concurrent)

    async def _remember_batch_locked(
        self, docs: list[RememberDoc], max_concurrent: int
    ) -> list[BatchDocResult]:
        """Batch body; caller holds ``self._lock`` (see :meth:`remember_batch`)."""
        results: dict[str, BatchDocResult] = {}

        async def run_batch(batch: list[RememberDoc]) -> None:
            payload = [doc_to_batch_dict(d) for d in batch]
            batch_result = await self.kb.remember_batch(
                payload,
                namespace=self.namespace_id,
                expertise=WC_EXPERTISE,
                entity_types=ENTITY_TYPES,
                relationship_types=RELATIONSHIP_TYPES,
                max_concurrent=max_concurrent,
            )
            # A graph-mirror degradation can leave the write lock held without
            # ever raising (#884). Surface it as a lock error so the caller's
            # reopen+retry path handles it before recording per-doc results.
            if _result_is_poisoned(batch_result):
                raise RuntimeError(
                    f"remember_batch reported a poisoning degradation "
                    f"({_LOCK_MARKER}/graph_mirror_failed) in batch metadata"
                )
            # remember_batch exposes no per-doc results, so read each doc's final
            # status back from the store and key success on COMPLETED.
            await self._collect_statuses(batch, results)

        try:
            await run_batch(docs)
        except Exception as exc:  # noqa: BLE001 - inspect, recover, fall back
            if not _is_lock_error(exc):
                raise
            logger.warning(
                "remember_batch hit write-lock error; reopening session and "
                "retrying the remaining docs once: %s",
                exc,
            )
            await self._reopen()
            remaining = [d for d in docs if d.external_id not in results]
            try:
                await run_batch(remaining)
            except Exception as exc2:  # noqa: BLE001 - last resort: serial
                if not _is_lock_error(exc2):
                    raise
                logger.warning(
                    "remember_batch retry still failing; falling back to serial "
                    "ingest for %d remaining docs: %s",
                    len(remaining),
                    exc2,
                )
                for doc in remaining:
                    if doc.external_id in results:
                        continue
                    try:
                        res = await self._remember_locked(doc)
                        results[doc.external_id] = BatchDocResult(
                            external_id=doc.external_id,
                            success=True,
                            doc_id=str(getattr(res, "document_id", "")) or None,
                        )
                    except Exception as doc_exc:  # noqa: BLE001 - isolate one doc
                        results[doc.external_id] = BatchDocResult(
                            external_id=doc.external_id,
                            success=False,
                            error=repr(doc_exc),
                        )

        # Any doc that never showed up (e.g. a store lookup gap) is a failure so
        # the watcher routes it to failed/ rather than silently dropping it.
        return [
            results.get(
                d.external_id,
                BatchDocResult(
                    external_id=d.external_id,
                    success=False,
                    error="no result returned for doc",
                ),
            )
            for d in docs
        ]

    async def _collect_statuses(
        self, batch: list[RememberDoc], results: dict[str, BatchDocResult]
    ) -> None:
        """Look up each doc's final status and record a per-doc result.

        One batched ``get_documents_by_external_ids`` read (negligible next to
        LLM extraction). A doc is a success iff its row exists and is COMPLETED;
        a missing or FAILED row is a failure carrying khora's ``error_message``.

        Namespace resolution (Postgres): every public ``kb`` API (remember,
        recall, remember_batch) resolves the STABLE ``namespace_id`` to the
        active VERSION's row id before touching child tables, and stamps that
        row id onto each document's ``namespace_id`` column. Our direct
        ``storage.*`` call bypasses that public layer, so we must resolve first
        ourselves — otherwise on Postgres the stable id never matches the row id
        on the document rows and EVERY just-ingested doc looks "not found". On
        the embedded backend the two ids coincide, so ``resolve_namespace`` is a
        cheap idempotent no-op there.
        """
        external_ids = [d.external_id for d in batch]
        resolved_ns = await self.kb.storage.resolve_namespace(self.namespace_id)
        by_ext = await self.kb.storage.get_documents_by_external_ids(
            external_ids, namespace_id=resolved_ns
        )
        for doc in batch:
            row = by_ext.get(doc.external_id)
            if row is None:
                results[doc.external_id] = BatchDocResult(
                    external_id=doc.external_id,
                    success=False,
                    error="document not found in store after batch",
                )
                continue
            status = getattr(row.status, "value", str(row.status))
            success = status == "completed"
            results[doc.external_id] = BatchDocResult(
                external_id=doc.external_id,
                success=success,
                doc_id=str(getattr(row, "id", "")) or None,
                error=None if success else (getattr(row, "error_message", None) or f"status={status}"),
            )

    async def recall(self, query: str, **kwargs) -> RecallResult:
        """Recall against the shared session, recovering once on a lock error."""
        async with self._lock:
            try:
                return await recall(self.kb, self.namespace_id, query, **kwargs)
            except Exception as exc:  # noqa: BLE001 - inspect, recover, re-raise
                if not _is_lock_error(exc):
                    raise
                logger.warning(
                    "recall hit write-lock error; reopening session and "
                    "retrying once: %s",
                    exc,
                )
                await self._reopen()
                return await recall(self.kb, self.namespace_id, query, **kwargs)

    async def stats(self) -> Stats:
        """Return namespace stats."""
        async with self._lock:
            return await self.kb.stats(namespace=self.namespace_id)

    async def _reopen(self) -> None:
        """Dispose the poisoned session and open a fresh one (lock held).

        Always called while already holding ``self._lock`` (never re-acquires it,
        so it cannot deadlock). The namespace UUID is persisted on disk, so the
        reopened session resolves to the same knowledge graph.
        """
        try:
            await self._cm.__aexit__(None, None, None)
        except Exception as exc:  # noqa: BLE001 - best effort; handle is poisoned
            logger.warning("error while closing poisoned khora session: %s", exc)

        self._cm = open_khora()
        self.kb, self.namespace_id = await self._cm.__aenter__()
        logger.info("KhoraRuntime reopened (namespace=%s)", self.namespace_id)

    async def aclose(self) -> None:
        """Close the underlying khora session."""
        async with self._lock:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - best effort on shutdown
                logger.warning("error while closing khora session: %s", exc)


# --- Process-wide singleton --------------------------------------------------
_runtime: KhoraRuntime | None = None
_runtime_lock = asyncio.Lock()


async def get_runtime() -> KhoraRuntime:
    """Return the lazily-created, process-wide :class:`KhoraRuntime`."""
    global _runtime
    if _runtime is None:
        async with _runtime_lock:
            if _runtime is None:
                _runtime = await KhoraRuntime.create()
    return _runtime


async def close_runtime() -> None:
    """Close and clear the process-wide runtime (call on app shutdown)."""
    global _runtime
    async with _runtime_lock:
        if _runtime is not None:
            await _runtime.aclose()
            _runtime = None
