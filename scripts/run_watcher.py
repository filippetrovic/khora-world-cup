"""Standalone inbox ingest runner.

Run a single pass (default) or a continuous watch loop:

    uv run python scripts/run_watcher.py            # one-shot: ingest + print counts
    uv run python scripts/run_watcher.py --watch    # poll forever (Ctrl-C to stop)
    uv run python scripts/run_watcher.py --watch --interval 10
    uv run python scripts/run_watcher.py --reingest # replay the WHOLE on-disk corpus
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

# Make the repo root importable when run as `uv run python scripts/run_watcher.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khora_wc.remember.watcher import process_inbox_once, watch_inbox
from khora_wc.runtime import KhoraRuntime


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest inbox docs into khora.")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="poll the inbox continuously instead of running a single pass",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="seconds between polls in --watch mode (default: 5.0)",
    )
    parser.add_argument(
        "--reingest",
        action="store_true",
        help="ignore ingested.json and re-remember every doc on disk, "
        "including those already in processed/ (they stay put). In --watch "
        "mode this applies to the first pass only.",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    runtime = await KhoraRuntime.create()
    try:
        if args.watch:
            stop_event = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, stop_event.set)
                except NotImplementedError:
                    # Signal handlers are unavailable on some platforms; Ctrl-C
                    # still raises KeyboardInterrupt and unwinds cleanly.
                    pass
            await watch_inbox(
                runtime,
                interval=args.interval,
                stop_event=stop_event,
                reingest=args.reingest,
            )
        else:
            counts = await process_inbox_once(runtime, reingest=args.reingest)
            print(
                f"ingested={counts['ingested']} "
                f"skipped={counts['skipped']} "
                f"failed={counts['failed']}"
            )
    finally:
        await runtime.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    args = _parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
