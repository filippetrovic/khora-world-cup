"""JSON-safe serialization of khora ``RecallResult`` objects.

The read API exposes the full recall trace to the UI, so every recall call is
turned into a plain ``dict`` with stringified UUIDs and ISO-8601 datetimes. Each
list is capped at ``max_items`` to keep responses bounded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from khora import RecallResult


def _iso(value: Any) -> str | None:
    """Return an ISO-8601 string for a datetime, else ``None``/passthrough."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _str_or_none(value: Any) -> str | None:
    """Stringify UUIDs (and other ids) while preserving ``None``."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def recall_result_to_dict(result: RecallResult, *, max_items: int = 12) -> dict:
    """Convert a :class:`RecallResult` into a JSON-safe ``dict``.

    Keys: ``chunks``, ``entities``, ``relationships``, ``documents`` (each capped
    at ``max_items``) and ``engine_info`` (which carries ``max_raw_vector_score``).
    """
    chunks = [
        {
            "content": chunk.content,
            "score": chunk.score,
            "document_id": _str_or_none(chunk.document_id),
            "occurred_at": _iso(chunk.occurred_at),
        }
        for chunk in (result.chunks or [])[:max_items]
    ]

    entities = [
        {
            "id": _str_or_none(entity.id),
            "name": entity.name,
            "entity_type": entity.entity_type,
            "description": entity.description,
            "score": entity.score,
        }
        for entity in (result.entities or [])[:max_items]
    ]

    # khora auto-creates generic co-occurrence edges that vastly outnumber the
    # typed ontology edges (DEFEATED, PLAYS_FOR, COACHES, ...). For the "what
    # khora returned" showcase we surface the typed relationships first and let
    # the generic ones fall to the bottom, and we resolve endpoint names so the
    # graph reads as "Mexico —[DEFEATED]→ South Africa" rather than raw ids.
    _generic_rels = {"CO_OCCURS_WITH", "ASSOCIATED_WITH"}
    _id2name = {str(e.id): e.name for e in (result.entities or [])}
    _ranked_rels = sorted(
        result.relationships or [],
        key=lambda r: (r.relationship_type in _generic_rels, -(r.score or 0.0)),
    )
    relationships = [
        {
            "relationship_type": rel.relationship_type,
            "source_entity_id": _str_or_none(rel.source_entity_id),
            "target_entity_id": _str_or_none(rel.target_entity_id),
            "source_name": _id2name.get(_str_or_none(rel.source_entity_id)),
            "target_name": _id2name.get(_str_or_none(rel.target_entity_id)),
            "description": rel.description,
            "score": rel.score,
        }
        for rel in _ranked_rels[:max_items]
    ]

    documents = [
        {
            "id": _str_or_none(doc.id),
            "title": doc.title,
            "source_type": doc.source_type,
            "source_name": doc.source_name,
            "source_url": doc.source_url,
            "source_timestamp": _iso(doc.source_timestamp),
            "external_id": doc.external_id,
        }
        for doc in (result.documents or [])[:max_items]
    ]

    engine_info = dict(result.engine_info or {})
    engine_info["max_raw_vector_score"] = engine_info.get("max_raw_vector_score")

    return {
        "chunks": chunks,
        "entities": entities,
        "relationships": relationships,
        "documents": documents,
        "engine_info": engine_info,
    }
