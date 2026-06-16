"""FastAPI router for the read side: /ask plus health/stats/ingest status."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from khora_wc.config import get_settings
from khora_wc.read.agent import answer_question
from khora_wc.runtime import get_runtime

logger = logging.getLogger(__name__)

router = APIRouter()


class AskRequest(BaseModel):
    """Body for ``POST /ask``."""

    question: str = Field(..., min_length=1)


@router.post("/ask")
async def ask(req: AskRequest) -> dict:
    """Answer a World Cup 2026 question, returning the locked response contract."""
    return await answer_question(req.question)


@router.get("/api/health")
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/api/stats")
async def stats() -> dict:
    """Namespace stats from the shared runtime."""
    runtime = await get_runtime()
    s = await runtime.stats()
    return {
        "documents": s.documents,
        "chunks": s.chunks,
        "entities": s.entities,
        "relationships": s.relationships,
        "last_activity_at": s.last_activity_at.isoformat()
        if s.last_activity_at
        else None,
    }


def _count_json(path) -> int:
    """Count ``*.json`` files recursively under ``path`` (0 if absent)."""
    if not path.exists():
        return 0
    return sum(1 for _ in path.rglob("*.json"))


@router.get("/api/ingest/status")
async def ingest_status() -> dict:
    """Counts of docs waiting in the inbox vs already processed vs failed."""
    settings = get_settings()
    return {
        "inbox": _count_json(settings.inbox_dir),
        "processed": _count_json(settings.processed_dir),
        "failed": _count_json(settings.failed_dir),
    }
