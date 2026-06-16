"""Run the FastAPI app via uvicorn programmatically.

Convenience entry point so ``uv run python scripts/run_app.py`` works with the
repo root on ``sys.path``. Equivalent to
``uv run uvicorn khora_wc.app:app --port 8000``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("khora_wc.app:app", host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
