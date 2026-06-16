"""CLI entry point for the per-match lineup ingestion pipeline.

Run with:  uv run python scripts/fetch_lineups.py [--max-fixtures N]

Lists World Cup fixtures, fetches each new fixture's starting XI from
API-Football (respecting the 100/day free-tier budget), writes new lineup docs
into the inbox, and prints the resulting counts plus a couple of example paths.

Needs API_FOOTBALL_KEY in .env; the client raises a clear error if it is unset.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the repo root importable when run as `uv run python scripts/fetch_lineups.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khora_wc.ingest.lineup.fetch import run


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch World Cup per-match starting lineups into the inbox."
    )
    parser.add_argument(
        "--max-fixtures",
        type=int,
        default=None,
        help="Cap the number of lineup API calls (and docs written) this run, "
        "to stay within the free-tier daily budget. Default: budget-limited.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable info-level logging (budget/skip diagnostics).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    result = run(max_fixtures=args.max_fixtures)

    print(
        f"written={result['written']} skipped={result['skipped']} "
        f"no_lineup={result['no_lineup']} budget_stopped={result['budget_stopped']}"
    )
    if result["examples"]:
        print("example files:")
        for path in result["examples"]:
            print(f"  {path}")
    if result["errors"]:
        print(f"errors ({len(result['errors'])}):")
        for err in result["errors"]:
            print(f"  {err}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
