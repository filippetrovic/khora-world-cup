"""Wipe the embedded khora store + ingest state for a from-scratch re-ingest.

This deletes ONLY the derived khora knowledge graph and the watcher's ingest
dedup state. It deliberately leaves the on-disk source of truth untouched:

  * REMOVED: the SQLite db (``data/khora/wc.db`` plus its ``-wal`` / ``-shm``
    siblings), the sibling LanceDB dir (``data/khora/wc.lance``), and the
    watcher dedup state (``data/state/ingested.json``).
  * KEPT:    every inbox/ + processed/ + failed/ JSON doc (the corpus we replay
    from) and the fetch seen-state (``match_seen.json`` / ``news_seen.json``).

After a reset, the next ``run_watcher.py`` pass re-``remember``s every doc on
disk from the beginning. Refuses to do anything without ``--yes``.

    uv run python scripts/reset_store.py --yes

The khora store is a single-writer SQLite db: do NOT run this while the watcher
or app is ingesting.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

# Make the repo root importable when run as `uv run python scripts/reset_store.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khora_wc.config import get_settings

logger = logging.getLogger(__name__)


def _reset_targets(settings) -> tuple[list[Path], list[Path]]:
    """Return ``(files, dirs)`` to remove.

    ``files``: the SQLite db + its WAL/SHM journal siblings, and ingested.json.
    ``dirs``:  the sibling LanceDB directory.
    """
    db_path = settings.khora_db_path
    files = [
        db_path,
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
        settings.state_dir / "ingested.json",
    ]
    # Sibling LanceDB dir lives next to the db with a .lance suffix (wc.db -> wc.lance).
    dirs = [db_path.with_suffix(".lance")]
    return files, dirs


def reset(settings, *, dry_run: bool = False) -> dict:
    """Remove the khora store + ingest state. Returns counts of what was removed."""
    files, dirs = _reset_targets(settings)

    removed_files: list[Path] = []
    removed_dirs: list[Path] = []
    missing: list[Path] = []

    for path in files:
        if path.exists():
            logger.info("%s file %s", "would remove" if dry_run else "removing", path)
            if not dry_run:
                path.unlink()
            removed_files.append(path)
        else:
            logger.info("skip (absent) %s", path)
            missing.append(path)

    for path in dirs:
        if path.exists():
            logger.info("%s dir  %s", "would remove" if dry_run else "removing", path)
            if not dry_run:
                shutil.rmtree(path)
            removed_dirs.append(path)
        else:
            logger.info("skip (absent) %s", path)
            missing.append(path)

    return {
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
        "missing": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wipe the khora store + ingested.json so the next watcher "
        "pass re-ingests every on-disk doc from scratch. Leaves inbox/processed "
        "docs and fetch seen-state intact."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required confirmation. Without it, nothing is removed.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    settings = get_settings()

    if not args.yes:
        files, dirs = _reset_targets(settings)
        print("Refusing to reset without --yes. Would remove:")
        for path in (*files, *dirs):
            print(f"  {path}")
        print("\nRe-run with --yes to actually delete the store + ingested.json.")
        print("(inbox/processed JSON docs and fetch seen-state are NOT touched.)")
        return 1

    result = reset(settings)

    print("\nReset complete.")
    print(f"  removed files = {len(result['removed_files'])}")
    print(f"  removed dirs  = {len(result['removed_dirs'])}")
    print(f"  absent (skip) = {len(result['missing'])}")
    print(
        "\nNext `uv run python scripts/run_watcher.py` will re-ingest every "
        "doc on disk from the beginning."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
