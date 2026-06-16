"""Single owning process for the World Cup 2026 read app.

One FastAPI process owns the khora store: a background task drains the inbox via
the watcher while HTTP endpoints answer questions through the same shared
runtime. khora is single-writer, so there is exactly one runtime singleton (see
``khora_wc.runtime``) and never a second session opened anywhere.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from khora_wc.config import REPO_ROOT
from khora_wc.read.api import router as read_router
from khora_wc.remember.watcher import process_inbox_once, watch_inbox
from khora_wc.runtime import close_runtime, get_runtime

logger = logging.getLogger(__name__)

WEB_DIST = REPO_ROOT / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Own the runtime + inbox watcher for the lifetime of the process."""
    logging.basicConfig(level=logging.INFO)

    runtime = await get_runtime()

    # Drain whatever is already in the inbox once so the store is warm before we
    # start answering questions.
    try:
        counts = await process_inbox_once(runtime)
        logger.info("startup inbox drain: %s", counts)
    except Exception:  # noqa: BLE001 - a bad inbox must not stop the app booting
        logger.exception("startup inbox drain failed")

    stop_event = asyncio.Event()
    watcher_task = asyncio.create_task(
        watch_inbox(runtime, interval=10, stop_event=stop_event)
    )

    try:
        yield
    finally:
        stop_event.set()
        try:
            await watcher_task
        except Exception:  # noqa: BLE001 - best effort on shutdown
            logger.exception("watcher task errored on shutdown")
        await close_runtime()
        logger.info("app shutdown complete")


app = FastAPI(title="khora World Cup 2026", lifespan=lifespan)

# Permissive CORS for local dev (the UI runs on a separate Vite port).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(read_router)

# Serve the built UI at / when it exists (built later by the UI agent). Guard so
# the app boots fine without it. Mounted last so the API routes win.
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
    logger.info("serving UI from %s", WEB_DIST)
else:
    logger.info("web/dist not found; running API only")
