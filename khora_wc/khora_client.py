"""Khora lifecycle + thin remember/recall wrappers for the World Cup app.

``open_khora`` is the single entry point every other module uses to obtain a
connected :class:`Khora` instance and the persistent namespace UUID. The
namespace UUID is created once and cached on disk so re-runs reuse the same
knowledge graph.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from khora import Khora, KhoraConfig, RecallResult, RememberResult

from khora_wc.config import Settings, configure_khora_env, get_settings
from khora_wc.contract import RememberDoc
from khora_wc.expertise import ENTITY_TYPES, RELATIONSHIP_TYPES, WC_EXPERTISE


def _namespace_state_path(settings: Settings) -> Path:
    return settings.state_dir / "namespace.json"


async def _resolve_or_create_namespace(kb: Khora, settings: Settings) -> UUID:
    """Return the persistent namespace UUID, creating + caching it if needed.

    The stable ``namespace_id`` is persisted to ``state_dir/namespace.json`` and
    validated with ``get_namespace_by_stable_id`` on every run; if the cached id
    no longer resolves to a live namespace we transparently create a new one.
    """
    state_path = _namespace_state_path(settings)

    if state_path.exists():
        try:
            cached = json.loads(state_path.read_text(encoding="utf-8"))
            namespace_id = UUID(cached["namespace_id"])
        except (ValueError, KeyError, json.JSONDecodeError):
            namespace_id = None
        if namespace_id is not None:
            existing = await kb.get_namespace_by_stable_id(namespace_id)
            if existing is not None:
                return existing.namespace_id

    # Create a fresh namespace and cache its stable id.
    ns = await kb.create_namespace()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {"namespace_id": str(ns.namespace_id), "label": settings.namespace_label},
            indent=2,
        ),
        encoding="utf-8",
    )
    return ns.namespace_id


@contextlib.asynccontextmanager
async def open_khora() -> AsyncIterator[tuple[Khora, UUID]]:
    """Open a connected Khora and yield ``(kb, namespace_id)``.

    Configures the embedded backend env vars, runs migrations on first open, and
    resolves/creates the persistent namespace.
    """
    settings = get_settings()
    configure_khora_env(settings)

    config = KhoraConfig()
    async with Khora(config, run_migrations=True) as kb:
        namespace_id = await _resolve_or_create_namespace(kb, settings)
        yield kb, namespace_id


async def remember_doc(
    kb: Khora, namespace_id: UUID, doc: RememberDoc
) -> RememberResult:
    """Remember a single :class:`RememberDoc` using the WC expertise."""
    return await kb.remember(
        doc.content,
        namespace=namespace_id,
        title=doc.title,
        source_type=doc.source_type,
        source_name=doc.source_name or None,
        source_url=doc.source_url,
        source_timestamp=doc.source_timestamp,
        metadata=doc.metadata,
        external_id=doc.external_id,
        expertise=WC_EXPERTISE,
        entity_types=ENTITY_TYPES,
        relationship_types=RELATIONSHIP_TYPES,
    )


async def recall(
    kb: Khora, namespace_id: UUID, query: str, **kwargs
) -> RecallResult:
    """Thin passthrough to ``kb.recall`` scoped to our namespace."""
    return await kb.recall(query, namespace=namespace_id, **kwargs)
