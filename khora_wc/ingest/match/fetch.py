"""Fetch + transform + dedup-write orchestrator for match data.

``run()`` makes at most three API calls (matches, standings, scorers),
transforms the payloads into ``RememberDoc``s, and writes only those whose
content has changed since the last run. The dedup state — a map of
``external_id -> sha256(content)`` — lives in ``data/state/match_seen.json`` so
re-running never re-``remember``s unchanged docs (each rewrite would trigger a
costly LLM re-extract downstream).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from khora_wc.config import Settings, get_settings
from khora_wc.contract import RememberDoc, write_doc
from khora_wc.ingest.match.client import FootballDataClient, PlanRestrictionError
from khora_wc.ingest.match.transform import (
    match_to_doc,
    scorers_to_doc,
    standings_to_docs,
)

logger = logging.getLogger(__name__)

STATE_FILENAME = "match_seen.json"


def _state_path(settings: Settings) -> Path:
    return settings.state_dir / STATE_FILENAME


def _load_state(settings: Settings) -> dict[str, str]:
    path = _state_path(settings)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read dedup state at %s; starting fresh", path)
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(settings: Settings, state: dict[str, str]) -> None:
    path = _state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _content_hash(doc: RememberDoc) -> str:
    return hashlib.sha256(doc.content.encode("utf-8")).hexdigest()


def _write_if_changed(
    settings: Settings,
    doc: RememberDoc,
    state: dict[str, str],
    written_paths: list[Path],
    *,
    full: bool = False,
) -> bool:
    """Write ``doc`` only if its content hash differs from the stored one.

    Returns True if written, False if skipped. Updates ``state`` and appends to
    ``written_paths`` on write. When ``full`` is True the dedup check is bypassed
    so the doc is always (re-)written and its hash refreshed in ``state``.
    """
    digest = _content_hash(doc)
    if not full and state.get(doc.external_id) == digest:
        return False
    path = write_doc(settings, doc)
    state[doc.external_id] = digest
    written_paths.append(path)
    return True


def run(limit_matches: int | None = None, *, full: bool = False) -> dict:
    """Fetch all WC data, transform, dedup, and write changed docs.

    Args:
        limit_matches: cap on the number of match docs to *consider* (and thus
            write) per run — for cheap testing. Standings/scorers are
            unaffected.
        full: when True, ignore the ``match_seen.json`` dedup state and
            re-write every fetched doc to the inbox (the seen-state is then
            refreshed). Default False keeps the skip-if-unchanged behavior.

    Returns a counts dict: ``{written, skipped, total, examples, errors}``.
    """
    settings = get_settings()
    state = _load_state(settings)

    written_paths: list[Path] = []
    written = 0
    skipped = 0
    errors: list[str] = []

    with FootballDataClient() as client:
        # --- Matches (the one mandatory call) -------------------------------
        matches_payload = client.get_matches()
        matches = matches_payload.get("matches") or []
        if limit_matches is not None:
            matches = matches[:limit_matches]
        logger.info("Fetched %d match objects", len(matches))

        for match in matches:
            try:
                doc = match_to_doc(match)
            except Exception as exc:  # noqa: BLE001 — one bad match must not kill the run
                errors.append(f"match {match.get('id')}: {exc}")
                logger.exception("Failed to transform match %s", match.get("id"))
                continue
            if _write_if_changed(settings, doc, state, written_paths, full=full):
                written += 1
            else:
                skipped += 1

        # --- Standings (best-effort) ----------------------------------------
        try:
            standings_payload = client.get_standings()
            for doc in standings_to_docs(standings_payload):
                if _write_if_changed(settings, doc, state, written_paths, full=full):
                    written += 1
                else:
                    skipped += 1
        except PlanRestrictionError as exc:
            errors.append(f"standings: {exc}")
            logger.warning("Standings unavailable: %s", exc)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"standings: {exc}")
            logger.exception("Standings fetch/transform failed")

        # --- Scorers (best-effort) ------------------------------------------
        try:
            scorers_payload = client.get_scorers(limit=20)
            doc = scorers_to_doc(scorers_payload)
            if _write_if_changed(settings, doc, state, written_paths, full=full):
                written += 1
            else:
                skipped += 1
        except PlanRestrictionError as exc:
            errors.append(f"scorers: {exc}")
            logger.warning("Scorers unavailable: %s", exc)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"scorers: {exc}")
            logger.exception("Scorers fetch/transform failed")

    _save_state(settings, state)

    return {
        "written": written,
        "skipped": skipped,
        "total": written + skipped,
        "examples": [str(p) for p in written_paths[:3]],
        "errors": errors,
    }
