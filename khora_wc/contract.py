"""Shared file contract: one JSON file == one ``remember`` payload.

Producers (match/news/seed fetchers) write ``RememberDoc`` JSON files into the
inbox; the remember worker reads them and calls ``kb.remember``. Keeping the
payload in a single typed model means producers and consumers never disagree on
the wire format.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from khora_wc.config import Settings

# Allowed provenance categories. Kept permissive (plain str) on the model so a
# new source type never hard-fails ingestion, but documented here.
SOURCE_TYPES = ("news", "match", "seed")


class RememberDoc(BaseModel):
    """A single unit of content to remember, plus its provenance metadata."""

    external_id: str
    content: str
    title: str = ""
    source_type: str  # one of SOURCE_TYPES: "news" | "match" | "seed"
    source_name: str = ""
    source_url: str | None = None
    source_timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to JSON with datetimes rendered as ISO-8601 strings."""
        return self.model_dump_json(indent=2)


_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def slug(external_id: str) -> str:
    """Turn an external_id into a filesystem-safe slug.

    Non-safe runs collapse to a single hyphen; leading/trailing separators are
    trimmed. Empty results fall back to ``"doc"`` so we never produce an
    empty filename.
    """
    cleaned = _SLUG_RE.sub("-", external_id).strip("-_.")
    return cleaned or "doc"


def _date_dir(doc: RememberDoc) -> str:
    """Return the ``YYYY-MM-DD`` partition for a doc (or ``"undated"``)."""
    if doc.source_timestamp is None:
        return "undated"
    return doc.source_timestamp.date().isoformat()


def doc_path(settings: Settings, doc: RememberDoc) -> Path:
    """Compute the canonical inbox path for a doc.

    ``<inbox>/<source_type>/<YYYY-MM-DD|undated>/<slug>.json``
    """
    return settings.inbox_for(doc.source_type) / _date_dir(doc) / f"{slug(doc.external_id)}.json"


def write_doc(settings: Settings, doc: RememberDoc) -> Path:
    """Atomically write a doc to its canonical inbox path; return the path.

    Writes to a temp file in the destination directory then ``os.replace``s it
    into place so readers never observe a partially written file.
    """
    path = doc_path(settings, doc)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(doc.to_json())
        os.replace(tmp_name, path)
    except BaseException:
        # Best-effort cleanup of the temp file on failure.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def read_doc(path: Path) -> RememberDoc:
    """Read and validate a doc JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return RememberDoc.model_validate(data)


def iter_inbox(settings: Settings) -> list[Path]:
    """Return all ``*.json`` files under the inbox, recursively, sorted."""
    inbox = settings.inbox_dir
    if not inbox.exists():
        return []
    return sorted(inbox.rglob("*.json"))
