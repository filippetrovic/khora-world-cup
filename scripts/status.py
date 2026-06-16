"""Unified ingest-pipeline status report.

Prints a human-readable summary of where every doc sits across the three
tracking layers:

  1. On disk   -- counts of JSON under inbox/ vs processed/ vs failed/, broken
                  down by source_type (match/news/seed) from the path layout.
  2. Fetched   -- entry counts in the 3rd-party dedup state (match_seen.json,
                  news_seen.json).
  3. Ingested  -- entry count in ingested.json, and (if the khora store is free)
                  live khora stats via a READ-ONLY open. If the store is busy/
                  locked, the live stats are skipped gracefully.

It also prints the "gap": on-disk docs whose external_id is not yet in
ingested.json (i.e. still pending ingestion).

    uv run python scripts/status.py

Read-only and safe to run any time -- it never opens the store for writing and
degrades gracefully when the store is busy.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# Make the repo root importable when run as `uv run python scripts/status.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khora_wc.config import Settings, get_settings
from khora_wc.contract import read_doc

# Known source_types (for stable column ordering); unknown ones still display.
_KNOWN_SOURCE_TYPES = ("match", "news", "seed")


def _source_type_of(root: Path, path: Path) -> str:
    """Infer a doc's source_type from its path: ``<root>/<source_type>/...``."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return "other"
    return rel.parts[0] if len(rel.parts) > 1 else "other"


def _scan_dir(root: Path) -> Counter:
    """Return a Counter of ``source_type -> json count`` under ``root``."""
    counts: Counter = Counter()
    if not root.exists():
        return counts
    for path in root.rglob("*.json"):
        counts[_source_type_of(root, path)] += 1
    return counts


def _ordered_types(*counters: Counter) -> list[str]:
    """Stable union of source_types seen across counters: known first, then rest."""
    seen = set()
    for counter in counters:
        seen.update(counter)
    extra = sorted(t for t in seen if t not in _KNOWN_SOURCE_TYPES)
    return [t for t in _KNOWN_SOURCE_TYPES if t in seen] + extra


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _match_seen_count(settings: Settings) -> int | None:
    data = _load_json(settings.state_dir / "match_seen.json")
    return len(data) if isinstance(data, dict) else None


def _news_seen_count(settings: Settings) -> int | None:
    data = _load_json(settings.state_dir / "news_seen.json")
    if isinstance(data, dict) and isinstance(data.get("seen"), list):
        return len(data["seen"])
    return None


def _ingested_state(settings: Settings) -> dict[str, str]:
    data = _load_json(settings.state_dir / "ingested.json")
    return data if isinstance(data, dict) else {}


def _disk_external_ids(*roots: Path) -> set[str]:
    """Collect external_ids of every JSON doc under the given roots."""
    ids: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            try:
                ids.add(read_doc(path).external_id)
            except Exception:  # noqa: BLE001 - a single bad doc shouldn't break status
                continue
    return ids


def _print_disk_section(settings: Settings) -> None:
    roots = {
        "inbox": settings.inbox_dir,
        "processed": settings.processed_dir,
        "failed": settings.failed_dir,
    }
    scans = {name: _scan_dir(root) for name, root in roots.items()}
    types = _ordered_types(*scans.values())

    print("On disk (JSON docs):")
    header = f"  {'state':<11}" + "".join(f"{t:>9}" for t in types) + f"{'total':>9}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, counter in scans.items():
        row = f"  {name:<11}" + "".join(f"{counter.get(t, 0):>9}" for t in types)
        row += f"{sum(counter.values()):>9}"
        print(row)
    grand_total = sum(sum(c.values()) for c in scans.values())
    print(f"  {'TOTAL':<11}" + " " * (9 * len(types)) + f"{grand_total:>9}")


def _print_fetched_section(settings: Settings) -> None:
    match_n = _match_seen_count(settings)
    news_n = _news_seen_count(settings)
    print("\nFetched (3rd-party dedup state):")
    print(f"  match_seen.json = {match_n if match_n is not None else '(absent/unreadable)'}")
    print(f"  news_seen.json  = {news_n if news_n is not None else '(absent/unreadable)'}")


def _print_ingested_section(settings: Settings, *, live_stats: bool = True) -> None:
    state = _ingested_state(settings)
    print("\nIngested (khora):")
    print(f"  ingested.json entries = {len(state)}")

    # Gap: docs on disk (inbox + processed) not yet recorded in ingested.json.
    disk_ids = _disk_external_ids(settings.inbox_dir, settings.processed_dir)
    pending = disk_ids - set(state)
    print(
        f"  on-disk docs (unique external_id) = {len(disk_ids)}; "
        f"pending ingestion (gap) = {len(pending)}"
    )

    # Live khora stats -- READ-ONLY, only if the store is free. The store is a
    # single-writer SQLite db; if a writer holds it we skip rather than crash.
    if live_stats:
        _print_live_khora_stats()
    else:
        print("  khora live stats: (skipped -- --no-live-stats)")


def _print_live_khora_stats() -> None:
    """Best-effort READ-ONLY khora stats; skip cleanly if the store is busy."""
    try:
        import asyncio

        from khora_wc.runtime import KhoraRuntime

        async def _go() -> object:
            runtime = await KhoraRuntime.create()
            try:
                return await runtime.stats()
            finally:
                await runtime.aclose()

        stats = asyncio.run(_go())
    except Exception as exc:  # noqa: BLE001 - busy/locked store must not crash status
        msg = str(exc).lower()
        if "lock" in msg or "busy" in msg:
            print("  khora live stats: (khora store busy -- skipped live stats)")
        else:
            print(f"  khora live stats: (skipped -- {exc})")
        return

    # Stats shape can vary across khora versions; read defensively.
    documents = getattr(stats, "documents", None)
    entities = getattr(stats, "entities", None)
    relationships = getattr(stats, "relationships", None)
    print(
        f"  khora live stats: documents={documents} "
        f"entities={entities} relationships={relationships}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified ingest-pipeline status report.")
    parser.add_argument(
        "--no-live-stats",
        action="store_true",
        help="Skip the READ-ONLY live khora stats open entirely (use when the "
        "store is known to be busy, or to avoid touching it at all).",
    )
    args = parser.parse_args()

    settings = get_settings()
    print("=" * 60)
    print("khora-world-cup ingest status")
    print("=" * 60)
    _print_disk_section(settings)
    _print_fetched_section(settings)
    _print_ingested_section(settings, live_stats=not args.no_live_stats)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
