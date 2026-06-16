"""CLI entry point for the match ingestion pipeline.

Run with:  uv run python scripts/fetch_matches.py [--limit N]

Fetches WC matches/standings/scorers, writes only changed docs into the inbox,
and prints the resulting counts plus a couple of example file paths.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the repo root importable when run as `uv run python scripts/fetch_matches.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khora_wc.ingest.match.fetch import run


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch World Cup match data into the inbox.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of match docs written (for cheap testing).",
    )
    parser.add_argument(
        "--full",
        "--no-skip",
        dest="full",
        action="store_true",
        help="Ignore match_seen.json and re-write every fetched doc "
        "(refreshes the seen-state). Default: skip unchanged docs.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable info-level logging (rate-limit/skip diagnostics).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    result = run(limit_matches=args.limit, full=args.full)

    print(
        f"written={result['written']} skipped={result['skipped']} "
        f"total={result['total']}"
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
