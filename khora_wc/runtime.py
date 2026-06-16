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
from uuid import UUID

from khora import Khora, RecallResult, RememberResult, Stats

from khora_wc.contract import RememberDoc
from khora_wc.khora_client import open_khora, recall, remember_doc

logger = logging.getLogger(__name__)

# Substring (case-insensitive) that marks the #884 embedded SQLite write-lock
# error. Once seen, the session handle is poisoned and must be reopened.
_LOCK_MARKER = "database is locked"


def _is_lock_error(exc: BaseException) -> bool:
    """True when an exception looks like the #884 write-lock error."""
    return _LOCK_MARKER in str(exc).lower()


def _result_is_poisoned(result: RememberResult) -> bool:
    """True when a (successful) RememberResult reports a poisoning degradation.

    On the replace path khora can mirror the graph-store failure as a
    ``graph_mirror_failed`` degradation under ``metadata["degradations"]`` rather
    than raising. Either form leaves the write lock held, so we treat it the same
    as a raised lock error and reopen before the next write.
    """
    degradations = result.metadata.get("degradations") or []
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
