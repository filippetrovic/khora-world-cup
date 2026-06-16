# khora-world-cup

A FIFA World Cup 2026 knowledge app built on the embedded [khora](https://pypi.org/project/khora/) store — a proof-of-concept for what khora makes easy.

Third-party feeds (match data + news) are fetched, transformed into khora-ready JSON, and remembered into a knowledge graph. A web UI lets you ask natural-language questions; a **pydantic-ai agent answers by calling khora `recall` as a tool**, and the page shows the answer, live retrieval metrics, and the full "what khora returned" trace (chunks, scores, typed entities & relationships, sources) so developers can see khora working.

```
3rd-party sources          inbox (JSON)         khora (embedded)         read side
┌───────────────┐  fetch  ┌────────────┐ watch ┌──────────────┐  recall ┌─────────────────────┐
│ football-data │ ──────▶ │ data/inbox │ ────▶ │  remember()  │ ◀────── │ pydantic-ai agent   │
│ RSS / GNews / │  +xform │  *.json    │       │ entities +   │         │  (recall as a tool) │
│ NewsData      │         └────────────┘       │ relationships│         │        │            │
└───────────────┘              │               └──────────────┘         │        ▼            │
                               ▼ on success                              │  FastAPI /ask       │
                         data/processed/  (durable on-disk record)       │  + React/Tailwind UI│
                                                                         └─────────────────────┘
```

Both the watcher and the API share **one** khora session (single-writer embedded SQLite + LanceDB) inside the FastAPI process.

## What it demonstrates

- **Grounded Q&A** over mixed structured (match results, standings, scorers) + unstructured (news) data.
- **Agentic retrieval** — the agent picks the search mode and filters (`source_type`, `occurred_at` window) itself, and may call `recall` more than once.
- **Knowledge graph** — typed entities (`TEAM`, `PLAYER`, `COACH`, `MATCH`, `GROUP`, `STADIUM`, …) and relationships (`DEFEATED`, `PLAYS_FOR`, `COACHES`, `PLAYED_AT`, `BELONGS_TO_GROUP`, …).
- **Temporal recall** — `source_timestamp` → `occurred_at`, queryable by date window.
- **Honest abstention** — when nothing relevant is retrieved, the agent says so instead of guessing.
- **Transparency** — every answer ships its recall trace + latency/score/token metrics.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (pins Python 3.13 automatically; khora requires ≥3.13).
- Node 20+ (only to build the web UI).
- A `.env` at the repo root (gitignored) — see `.env.example`:
  - `OPENAI_API_KEY` — used by khora for embeddings (`text-embedding-3-small`) + entity extraction (`gpt-4o-mini`), and by the read-side answer agent.
  - `DATA_FOOTBALL_TOKEN` — football-data.org free-tier token (match data).
  - `NEWSDATA_TOKEN` — NewsData.io token (best-effort news supplement).

## Setup

```bash
uv sync                              # Python deps
npm --prefix web install             # UI deps
npm --prefix web run build           # build web/dist (FastAPI serves it at /)
```

## Storage backend

khora can run against two backends, selected by the `KHORA_BACKEND` env var:

- **`postgres`** (default) — Postgres + pgvector (relational + vector) and Neo4j
  (graph), brought up locally with Docker via `compose.yaml`.
- **`embedded`** — the zero-infra, in-process `sqlite_lance` store at
  `data/khora/wc.db` (kept as a fallback; each backend keeps its own namespace
  state file under `data/state/`).

```bash
docker compose up -d     # start Postgres (5432) + Neo4j (7474 http / 7687 bolt)
docker compose ps        # wait until both report (healthy)
docker compose down      # stop (add -v to also drop the data volumes)
```

Local dev credentials are baked into `compose.yaml` (`khora` / `khora_dev`) and
matched by defaults in `khora_wc/config.py`, so `KHORA_BACKEND=postgres` works
with no extra env. The Postgres path pins the embedding dimension to 1536
(`text-embedding-3-small`), which khora's pgvector schema requires. Use
`KHORA_BACKEND=embedded` to fall back to the SQLite store.

## Quickstart (end to end)

```bash
# 1. Fetch third-party data into data/inbox/ (writes khora-ready JSON, dedup-aware)
uv run python scripts/fetch_matches.py            # all WC matches + standings + scorers
uv run python scripts/fetch_news.py --mode recent # live RSS + Google News (use --mode all to backfill to ~May 11)

# 2. Ingest the inbox into khora (entity/relationship extraction; ~6s/doc)
uv run python scripts/run_watcher.py

# 3. Run the app (serves the UI + the /ask API)
uv run uvicorn khora_wc.app:app --port 8000
# open http://localhost:8000
```

On startup the app drains any pending inbox docs and then keeps watching the inbox, so you can re-run the fetchers anytime and it ingests new content in the background. Check progress with `uv run python scripts/status.py`.

## Try these questions

- *What was the score in the Mexico match?*
- *Who are the top scorers at the World Cup?*
- *How does Group A look so far?*
- *Which teams have won their matches and who did they beat?*
- *What's the latest World Cup news?*
- *Who won the Ballon d'Or in 2019?* — (out of scope → the agent abstains)

## Read API

- `POST /ask` `{"question": "..."}` → `{answer, abstained, metrics, recall_trace}`
  - `metrics`: `recall_latency_ms`, `total_latency_ms`, `recall_calls`, `top_score`, `max_raw_vector_score`, `answer_tokens`.
  - `recall_trace`: one entry per `recall` call — the params the agent chose, latency, and the serialized khora result (chunks + scores, typed entities, named relationships, source documents).
- `GET /api/stats` — store size (`documents`/`entities`/`relationships`).
- `GET /api/health`, `GET /api/ingest/status`.

## Project layout

```
khora_wc/
  config.py          settings (.env), data paths, khora env wiring
  contract.py        RememberDoc — the inbox JSON schema
  expertise.py       loads config/worldcup_expertise.yaml (the WC ontology)
  khora_client.py    open_khora(), remember_doc(), recall()
  runtime.py         KhoraRuntime — one shared, lock-serialized session (auto-reopen)
  ingest/match/      football-data.org client + match→prose transform
  ingest/news/       RSS + Google News (date-scoped) + NewsData + article→prose transform
  remember/watcher.py  inbox → khora, dedup + processed/ persistence
  read/agent.py      pydantic-ai answer agent (recall as a tool)
  read/api.py        FastAPI routes
  read/serialize.py  RecallResult → JSON (typed relationships first, names resolved)
  app.py             FastAPI app: shared runtime + background watcher + UI mount
config/worldcup_expertise.yaml   entity/relationship ontology + extraction prompt
web/                 Vite + React + Tailwind UI (build → web/dist)
scripts/             fetch_matches, fetch_news, run_watcher, status, reset_store, run_app
```

## Data tracking & overrides

The pipeline tracks state across three layers so re-runs never repeat expensive
work. Each layer has an override for when you *want* to force a redo.

### The three state files (under `data/state/`)

| File | Written by | Tracks | Shape |
| --- | --- | --- | --- |
| `match_seen.json` | `scripts/fetch_matches.py` | match docs already written to the inbox | `external_id -> sha256(content)` |
| `news_seen.json` | `scripts/fetch_news.py` | news articles already written to the inbox | `{"seen": [external_id, ...]}` |
| `ingested.json` | `scripts/run_watcher.py` | docs already remembered into khora | `external_id -> sha256(content)` |

`match_seen.json` / `news_seen.json` gate the **fetchers** (don't re-write
unchanged third-party content). `ingested.json` gates the **watcher** (don't
re-`remember` unchanged docs — a re-remember triggers a full, costly LLM
re-extract, so this is a real skip, not a cheap one).

### Where ingested JSON lives

The JSON docs are the on-disk source of truth and they **persist** after
ingestion. The watcher moves each file inbox -> `data/processed/` on success
(or -> `data/failed/` on error) — it never deletes a doc. So
`data/processed/<source_type>/<date>/<slug>.json` is the durable record of
everything that has been ingested, and the khora store can always be rebuilt
from it.

### Override commands

| Goal | Command |
| --- | --- |
| Re-fetch & re-write **everything** (ignore fetch seen-state) | `uv run python scripts/fetch_matches.py --full`<br>`uv run python scripts/fetch_news.py --full` |
| Re-ingest **everything on disk** into khora (ignore `ingested.json`; replays `inbox/` **and** `processed/` in place) | `uv run python scripts/run_watcher.py --reingest` |
| Wipe the khora store + `ingested.json` for a from-scratch re-ingest (keeps all docs + fetch seen-state) | `uv run python scripts/reset_store.py --yes`<br>then `uv run python scripts/run_watcher.py` |
| See a unified status report (on-disk / fetched / ingested counts + gap) | `uv run python scripts/status.py` |

Notes:

- `--full` accepts `--no-skip` as an alias. It refreshes the seen-state after
  re-writing, so the *next* default run returns to skip-if-unchanged behavior.
- `--reingest` re-reads docs already in `processed/` and remembers them again
  **in place** (they are not moved). khora upserts on `external_id`, so the
  prior version is replaced.
- `reset_store.py` refuses to do anything without `--yes`. It removes only the
  derived store (`data/khora/wc.db` + its `-wal`/`-shm` journals, the sibling
  `data/khora/wc.lance` dir) and `data/state/ingested.json`. It never touches
  the inbox/processed JSON docs or the fetch seen-state.
- `status.py` is read-only. It attempts live khora stats via a READ-ONLY open;
  if the store is busy/locked it prints `(khora store busy -- skipped live
  stats)` instead of failing. Pass `--no-live-stats` to skip that open entirely.
- The khora store is a single-writer SQLite db. Do **not** run `reset_store.py`
  or a `--reingest` while the watcher or app is actively ingesting.

## Notes & known limitations

- **Single-writer store.** All khora access (ingest + queries) serializes through one lock, so `/ask` latency spikes (multi-second → ~10s+) while a large ingest is running, then settles to sub-second warm recall once the inbox is drained.
- **Free-tier match data.** football-data.org free tier delivers scores with a short delay (not true live) and omits per-match goal/card/lineup arrays; goal scorers come from the tournament-wide `/scorers` endpoint instead.
- **News coverage.** RSS feeds only expose a rolling recent window; backfill toward ~May 11 is done with Google News date-scoped queries, and NewsData.io is a best-effort 48h/200-credits-per-day supplement (no historical archive on the free tier).
- **Graph edges.** khora auto-creates many generic `CO_OCCURS_WITH`/`ASSOCIATED_WITH` co-occurrence edges; the read API sorts the typed ontology edges first so the "what khora returned" view stays meaningful.
