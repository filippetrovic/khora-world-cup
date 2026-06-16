"""Smoke test proving the khora foundation works end to end.

Run with:  uv run python scripts/smoke_test.py

Exercises remember, recall, upsert-via-external_id, the byte-identical no-op,
and source_timestamp -> occurred_at temporal filtering. Content is kept tiny to
minimize OpenAI cost.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

# Make the repo root importable when run as `uv run python scripts/smoke_test.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khora import context_text

from khora_wc.contract import RememberDoc
from khora_wc.expertise import ENTITY_TYPES, RELATIONSHIP_TYPES, WC_EXPERTISE
from khora_wc.khora_client import open_khora, recall, remember_doc


def _hr(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def _print_remember(label: str, result) -> None:
    print(
        f"[{label}] chunks={result.chunks_created} "
        f"entities={result.entities_extracted} "
        f"relationships={result.relationships_created} "
        f"metadata={result.metadata}"
    )


def _print_top_chunk(result) -> None:
    if not result.chunks:
        print("  (no chunks returned)")
        return
    top = result.chunks[0]
    print(f"  top score={top.score:.4f}")
    print(f"  top content={top.content!r}")
    print(f"  max_raw_vector_score={result.engine_info.get('max_raw_vector_score')}")


async def main() -> None:
    # We deliberately open a FRESH Khora session per phase. Rationale: khora's
    # embedded sqlite_lance replace path can hit a known graph-mirror
    # IntegrityError (#884) that leaves the shared SQLite handle holding the
    # write lock for the rest of that session ("database is locked"). Closing
    # and reopening the session disposes the poisoned handle and clears the
    # lock — the only reliable in-app recovery. The persistent namespace is
    # shared across every session, so this is seamless and mirrors how the real
    # app runs (each watcher batch / API request opens its own session).

    match_doc = RememberDoc(
        external_id="match:test:1",
        title="Canada vs Mexico",
        content="Canada beat Mexico 1 - 0 in the group stage. Jonathan David scored for Canada at 30'.",
        source_type="match",
        source_name="smoke",
        source_timestamp=datetime(2026, 6, 12, 19, 0, tzinfo=UTC),
    )
    match_doc_v2 = match_doc.model_copy(
        update={
            "content": (
                "Canada beat Mexico 2 - 1 in the group stage. "
                "Jonathan David scored twice for Canada at 30' and 75'."
            )
        }
    )

    # --- (a) remember a MATCH doc + (b) recall the scoreline --------------
    async with open_khora() as (kb, namespace_id):
        print(f"namespace_id={namespace_id}")

        _hr("(a) REMEMBER match:test:1  (Canada 1 - 0 Mexico)")
        res_a = await remember_doc(kb, namespace_id, match_doc)
        _print_remember("a", res_a)

        _hr("(b) RECALL  'What was the score between Canada and Mexico?'")
        t0 = perf_counter()
        rec_b = await recall(kb, namespace_id, "What was the score between Canada and Mexico?")
        latency_ms = (perf_counter() - t0) * 1000.0
        print(f"  recall latency = {latency_ms:.1f} ms")
        _print_top_chunk(rec_b)

    # --- (c) UPSERT same external_id with new scoreline -------------------
    async with open_khora() as (kb, namespace_id):
        _hr("(c) UPSERT match:test:1  (Canada 2 - 1 Mexico)")
        res_c = await remember_doc(kb, namespace_id, match_doc_v2)
        _print_remember("c", res_c)

    # Verify the upsert in a fresh session (the replace above may poison the
    # write lock; reads + the next writes need a clean handle).
    async with open_khora() as (kb, namespace_id):
        rec_c = await recall(kb, namespace_id, "What was the score between Canada and Mexico?")
        joined = " ".join(c.content for c in rec_c.chunks)
        print(f"  recalled content now contains '2 - 1': {'2 - 1' in joined}")
        print(f"  recalled content still contains stale '1 - 0': {'1 - 0' in joined}")
        _print_top_chunk(rec_c)

        # --- (d) NO-OP: byte-identical re-remember ------------------------
        # NOTE: khora's checksum no-op (metadata.duplicate=True) fires on the
        # *checksum* path, which is only reached when the external_id is NOT
        # already present. When an external_id already maps to a stored row,
        # khora ALWAYS routes to the replace path (metadata.replaced=True) and
        # never short-circuits on an unchanged checksum. So to prove the genuine
        # byte-identical no-op we use a fresh doc with NO external_id and send
        # the same content twice. (See report: deviation from the brief.)
        _hr("(d) NO-OP  remember identical content twice (checksum dedup, no external_id)")
        noop_content = "Morocco drew 1 - 1 with Spain in a 2026 World Cup group game."
        noop_kwargs = dict(
            namespace=namespace_id,
            title="Morocco vs Spain",
            source_type="match",
            source_name="smoke",
            source_timestamp=datetime(2026, 6, 18, 16, 0, tzinfo=UTC),
            entity_types=ENTITY_TYPES,
            relationship_types=RELATIONSHIP_TYPES,
            expertise=WC_EXPERTISE,
        )
        first = await kb.remember(noop_content, **noop_kwargs)
        _print_remember("d-first", first)
        second = await kb.remember(noop_content, **noop_kwargs)
        _print_remember("d-second", second)
        is_dup = bool(second.metadata.get("duplicate"))
        print(f"  duplicate flag set on 2nd identical remember: {is_dup}")

    # --- (e) TEMPORAL: two news docs, bound to June only ------------------
    async with open_khora() as (kb, namespace_id):
        _hr("(e) TEMPORAL filtering  (May vs June news)")
        news_may = RememberDoc(
            external_id="news:test:may",
            title="May squad news",
            content="In May 2026, Brazil announced its preliminary World Cup squad.",
            source_type="news",
            source_name="smoke",
            source_timestamp=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        )
        news_june = RememberDoc(
            external_id="news:test:june",
            title="June squad news",
            content="On 14 June 2026, Brazil finalized its World Cup starting eleven.",
            source_type="news",
            source_name="smoke",
            source_timestamp=datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
        )
        res_may = await remember_doc(kb, namespace_id, news_may)
        res_june = await remember_doc(kb, namespace_id, news_june)
        _print_remember("e-may", res_may)
        _print_remember("e-june", res_june)

        # Bound the recall to a June-only window via the event-time-precise
        # filter API (occurred_at, derived from source_timestamp). The
        # deprecated start_time/end_time kwargs are only a soft recency window
        # and do NOT hard-filter event time, so we use filter= instead.
        rec_e = await recall(
            kb,
            namespace_id,
            "Brazil World Cup squad",
            filter={
                "source_type": "news",
                "occurred_at": {
                    "$gte": datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
                    "$lt": datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
                },
            },
        )
        occurred = [
            (c.occurred_at.isoformat() if c.occurred_at else None, c.content[:60])
            for c in rec_e.chunks
        ]
        print(f"  June-bounded recall returned {len(rec_e.chunks)} chunk(s):")
        for ts, snippet in occurred:
            print(f"    occurred_at={ts}  content={snippet!r}")
        joined_e = " ".join(c.content for c in rec_e.chunks)
        print(f"  contains June doc: {'14 June' in joined_e or 'finalized' in joined_e}")
        print(f"  excludes May doc:  {'In May' not in joined_e}")

        # --- final stats --------------------------------------------------
        _hr("STATS")
        stats = await kb.stats(namespace=namespace_id)
        print(
            f"  documents={stats.documents} chunks={stats.chunks} "
            f"entities={stats.entities} relationships={stats.relationships} "
            f"last_activity_at={stats.last_activity_at}"
        )

        # --- rendered context preview (sanity) ----------------------------
        _hr("context_text(rec_e) preview")
        print(context_text(rec_e, max_chunks=3))


if __name__ == "__main__":
    asyncio.run(main())
