"""CLI entry point for the Wikipedia squad ingestion pipeline.

Run with:  uv run python scripts/fetch_squads.py [--limit N] [--dry-run]

Fetches every World Cup 2026 squad from Wikipedia, turns each into a
``RememberDoc``, and writes them into the inbox for the remember worker to pick
up. ``--dry-run`` parses and prints without touching the inbox.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the repo root importable when run as `uv run python scripts/fetch_squads.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khora_wc.config import get_settings
from khora_wc.contract import write_doc
from khora_wc.ingest.squads.transform import squad_to_doc
from khora_wc.ingest.squads.wikipedia import fetch_squads

log = logging.getLogger("fetch_squads")


def run(*, limit: int | None = None, dry_run: bool = False) -> dict:
    """Fetch + transform + (optionally) write squad docs.

    Returns a summary dict: ``{teams, players, written, examples}``. With
    ``dry_run`` no files are written and ``written`` is 0.
    """
    squads = fetch_squads()
    if limit is not None:
        squads = squads[:limit]

    settings = get_settings()
    total_players = sum(len(s.players) for s in squads)
    examples: list[str] = []
    written = 0

    for squad in squads:
        doc = squad_to_doc(squad)
        if dry_run:
            log.info("[dry-run] %s (%d players)", doc.external_id, len(squad.players))
            continue
        path = write_doc(settings, doc)
        written += 1
        if len(examples) < 3:
            examples.append(str(path))

    return {
        "teams": len(squads),
        "players": total_players,
        "written": written,
        "examples": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch World Cup 2026 squads from Wikipedia into the inbox."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of squads processed (for cheap testing).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report without writing any inbox files.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable info-level logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    result = run(limit=args.limit, dry_run=args.dry_run)

    print(
        f"teams={result['teams']} players={result['players']} "
        f"written={result['written']}"
    )
    if result["examples"]:
        print("example files:")
        for path in result["examples"]:
            print(f"  {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
