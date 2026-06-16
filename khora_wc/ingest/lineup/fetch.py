"""Fetch + transform + dedup-write orchestrator for per-match lineups.

``run()`` lists the World Cup fixtures (one API call), then for each fixture
whose lineups we have not already captured, fetches its starting XI (one API
call), transforms it, and writes new docs into the inbox. Dedup state — a map of
``external_id -> sha256(content)`` — lives in ``data/state/lineup_seen.json`` so
re-running never re-fetches or re-``remember``s an already-captured fixture.

Cost discipline (free tier = 100 requests/day):

* Fixtures already in the seen state are skipped *before* spending a lineup
  call on them.
* The orchestrator stops fetching once the client's reported daily budget
  drops to its reserve floor, or once ``max_fixtures`` lineup calls have been
  made this run.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from khora_wc.config import Settings, get_settings
from khora_wc.contract import RememberDoc, write_doc
from khora_wc.ingest.lineup.client import ApiFootballClient
from khora_wc.ingest.lineup.transform import lineups_to_doc

logger = logging.getLogger(__name__)

STATE_FILENAME = "lineup_seen.json"
# Stop spending lineup calls once the daily budget is this low, leaving slack
# for other pipelines sharing the same key.
DAILY_BUDGET_FLOOR = 5


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


def _fixture_external_id(fixture: dict) -> str | None:
    fixture_node = fixture.get("fixture") or {}
    fixture_id = fixture_node.get("id")
    return f"lineup:af:{fixture_id}" if fixture_id is not None else None


def run(max_fixtures: int | None = None) -> dict:
    """List WC fixtures, fetch+transform+write new lineups within the budget.

    Args:
        max_fixtures: cap on the number of *lineup* API calls (and thus writes)
            this run. ``None`` means "as many as the daily budget allows".

    Returns a counts dict:
        ``{written, skipped, no_lineup, budget_stopped, examples, errors}``.
    """
    settings = get_settings()
    state = _load_state(settings)

    written_paths: list[Path] = []
    written = 0
    skipped = 0
    no_lineup = 0
    budget_stopped = False
    errors: list[str] = []

    with ApiFootballClient() as client:
        try:
            fixtures = client.get_fixtures()
        except Exception as exc:  # noqa: BLE001 — surface any fetch failure as a clean result
            logger.exception("Failed to list fixtures")
            return {
                "written": 0,
                "skipped": 0,
                "no_lineup": 0,
                "budget_stopped": False,
                "examples": [],
                "errors": [f"fixtures: {exc}"],
            }

        logger.info("Fetched %d World Cup fixtures", len(fixtures))

        lineup_calls = 0
        for fixture in fixtures:
            external_id = _fixture_external_id(fixture)
            if external_id is None:
                continue

            # Already captured — skip without spending a lineup call.
            if external_id in state:
                skipped += 1
                continue

            # Respect the per-run cap on lineup calls.
            if max_fixtures is not None and lineup_calls >= max_fixtures:
                budget_stopped = True
                logger.info("Reached max_fixtures=%d; stopping", max_fixtures)
                break

            # Respect the daily budget floor (None = unknown, proceed).
            if (
                client.requests_remaining is not None
                and client.requests_remaining <= DAILY_BUDGET_FLOOR
            ):
                budget_stopped = True
                logger.info(
                    "Daily budget at %d (<= floor %d); stopping",
                    client.requests_remaining,
                    DAILY_BUDGET_FLOOR,
                )
                break

            fixture_id = (fixture.get("fixture") or {}).get("id")
            try:
                lineups = client.get_lineups(fixture_id)
                lineup_calls += 1
                doc = lineups_to_doc(fixture, lineups)
            except Exception as exc:  # noqa: BLE001 — one bad fixture must not kill the run
                errors.append(f"fixture {fixture_id}: {exc}")
                logger.exception("Failed to fetch/transform lineups for %s", fixture_id)
                continue

            if doc is None:
                # Lineups not published yet — leave unseen so a later run retries.
                no_lineup += 1
                continue

            path = write_doc(settings, doc)
            state[doc.external_id] = _content_hash(doc)
            written_paths.append(path)
            written += 1

    _save_state(settings, state)

    return {
        "written": written,
        "skipped": skipped,
        "no_lineup": no_lineup,
        "budget_stopped": budget_stopped,
        "examples": [str(p) for p in written_paths[:3]],
        "errors": errors,
    }
