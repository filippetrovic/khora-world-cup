"""Crawl WC-2026-relevant Wikipedia articles into the inbox.

Run with:
    uv run python scripts/fetch_wiki.py
    uv run python scripts/fetch_wiki.py --max-depth 1 --cap 200
    uv run python scripts/fetch_wiki.py --full -v

Bounded BFS from "2026 FIFA World Cup" over the MediaWiki API, dedups against
``data/state/wiki_seen.json``, and writes new ``RememberDoc`` JSON files under
``data/inbox/wiki/``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the repo root importable when run as `uv run python scripts/fetch_wiki.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khora_wc.ingest.wiki.fetch import run


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Crawl WC-2026 Wikipedia articles into the inbox."
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=1,
        help="Link-hops from the seed to follow. 0 = seed only; "
        "1 (default) = seed + the articles it links to.",
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=300,
        help="Hard ceiling on pages fetched this run (default 300).",
    )
    parser.add_argument(
        "--full",
        "--no-skip",
        dest="full",
        action="store_true",
        help="Ignore wiki_seen.json and re-write every crawled article "
        "(refreshes the seen-state). Default: skip already-seen articles.",
    )
    parser.add_argument(
        "--all-links",
        dest="all_links",
        action="store_true",
        help="Disable the WC-2026 relevance filter and follow EVERY non-noise "
        "link (incl. the pre-2026 historical backlog). Default: WC-2026 only.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable INFO-level logging from the crawler.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    counts = run(
        max_depth=args.max_depth,
        cap=args.cap,
        full=args.full,
        all_links=args.all_links,
    )

    print(f"\nWiki fetch (max_depth={args.max_depth}, cap={args.cap}):")
    print(f"  written      = {counts['written']}")
    print(f"  skipped      = {counts['skipped']} (already seen)")
    print(f"  filtered_out = {counts['filtered_out']} (empty / too short)")
    print(f"  total_seen   = {counts['total_seen']}")

    examples = counts.get("examples") or []
    if examples:
        print("\nExample files written:")
        for path in examples:
            print(f"  {path}")
    else:
        print("\n(No new files written.)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
