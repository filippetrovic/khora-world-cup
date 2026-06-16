"""Fetch World Cup news into the inbox.

Run with:
    uv run python scripts/fetch_news.py --mode recent --limit 15
    uv run python scripts/fetch_news.py --mode backfill
    uv run python scripts/fetch_news.py --mode gdelt --limit 2000 --per-window-limit 30
    uv run python scripts/fetch_news.py --mode all --limit 2000 --per-window-limit 30

Fetches GDELT / RSS / Google News / NewsData, dedups against
``data/state/news_seen.json``, and writes new ``RememberDoc`` JSON files under
``data/inbox/news/<date>/``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the repo root importable when run as `uv run python scripts/fetch_news.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khora_wc.ingest.news.fetch import run


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch World Cup news into the inbox.")
    parser.add_argument(
        "--mode",
        choices=("recent", "backfill", "gdelt", "all"),
        default="recent",
        help="recent: RSS + 7-day Google News (default); "
        "backfill: weekly Google News windows + NewsData; "
        "gdelt: bulk GDELT DOC fan-out (May 11->today, async-enriched); "
        "all: gdelt + recent + backfill (the ~2k path).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=60,
        help="Run-wide max NEW docs to write this run (cost control). 0 = no cap.",
    )
    parser.add_argument(
        "--per-window-limit",
        type=int,
        default=0,
        help="Per-group cap: max docs from EACH group (the recent group and "
        "every weekly backfill window) so recent news can't starve older "
        "windows. 0 = no per-window cap. Use with --mode all to span a month.",
    )
    parser.add_argument(
        "--full",
        "--no-skip",
        dest="full",
        action="store_true",
        help="Ignore news_seen.json and re-write every fetched article "
        "(refreshes the seen-state). Default: skip already-seen articles.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable INFO-level logging from the fetchers.",
    )
    args = parser.parse_args()

    console_level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(level=console_level, format="%(levelname)s %(name)s: %(message)s")
    # Pin the console handler's level explicitly. The fetch run lowers the news
    # package logger to INFO for its file sink (data/logs/news_fetch.log); without
    # an explicit level the console handler would print those propagated INFO
    # lines too. Setting it here keeps the console exactly as verbose as -v asked.
    for handler in logging.getLogger().handlers:
        handler.setLevel(console_level)

    limit = None if args.limit == 0 else args.limit
    per_window_limit = None if args.per_window_limit == 0 else args.per_window_limit
    counts = run(
        mode=args.mode,
        limit=limit,
        per_window_limit=per_window_limit,
        full=args.full,
    )

    print(f"\nNews fetch ({args.mode}, limit={limit}, per_window={per_window_limit}):")
    print(f"  written      = {counts['written']}")
    print(f"  skipped      = {counts['skipped']} (already seen)")
    print(f"  filtered_out = {counts['filtered_out']} (non-WC / empty)")
    print(f"  enriched     = {counts.get('enriched', 0)} (thin body -> full article)")
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
