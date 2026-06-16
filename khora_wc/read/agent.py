"""pydantic-ai answer agent for FIFA World Cup 2026 questions.

A single module-level :class:`Agent` answers each question using ONLY content
returned by the ``recall`` tool, which the model is free to call (and re-call)
with whatever search mode / filters fit the question. khora supplies grounded
context; the agent writes the final answer or abstains.

Every recall call is recorded in ``AskDeps.trace`` so the API can expose the full
retrieval trace and aggregate metrics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter

from pydantic_ai import Agent, RunContext

from khora import SearchMode, context_text

from khora_wc.config import get_settings
from khora_wc.read.serialize import recall_result_to_dict
from khora_wc.runtime import KhoraRuntime, get_runtime

logger = logging.getLogger(__name__)

# Map the loose mode string the model may pass to a concrete SearchMode.
_MODE_MAP = {
    "vector": SearchMode.VECTOR,
    "graph": SearchMode.GRAPH,
    "hybrid": SearchMode.HYBRID,
    "all": SearchMode.ALL,
    "keyword": SearchMode.KEYWORD,
}

# Phrases the agent is told to use when it has no grounding; used to flag
# abstention heuristically.
_ABSTAIN_MARKERS = (
    "don't have that information",
    "do not have that information",
    "don't have information",
    "do not have information",
    "no information",
    "couldn't find",
    "could not find",
    "i don't have",
    "i do not have",
    "not available in",
    "no relevant",
)

SYSTEM_PROMPT = """\
You are the FIFA World Cup 2026 answer agent. It is mid-tournament (June 2026).

You answer questions ONLY from content returned by the `recall` tool, which
searches a khora knowledge store of World Cup 2026 news articles plus structured
match data (scores, fixtures, group standings, top scorers).

Rules:
- You MUST call `recall` at least once before answering. Never answer from prior
  knowledge or assumptions.
- Choose the search mode and filters that fit the question:
  - Use source_type='match' for anything factual about scores, results, fixtures,
    standings, or who plays whom. Use source_type='news' for narrative, analysis,
    storylines, or opinion. Omit source_type when unsure.
  - For time-scoped questions ("on June 14", "yesterday", "this week") set
    occurred_after / occurred_before (ISO date or datetime, UTC).
  - Default mode is 'hybrid'. Use 'vector' for fuzzy/semantic questions,
    'keyword' for exact names/phrases, 'graph' for relationship questions.
  - For broad / aggregate / list questions ("which teams won", "list all
    results so far", "who has qualified", "show every Group B match"), the
    answer is spread across MANY individual match documents (each says e.g.
    "Mexico 2, South Africa 0. Mexico beat South Africa."). Retrieve broadly:
    use source_type='match' with a HIGH limit (30-40) and synthesize across
    every returned match. Do NOT abstain when match documents come back — read
    the results out of them; only finished matches have a winner.
- If the first recall is weak, refine: rephrase the query, switch mode, widen the
  time window, or drop the source_type filter, then call recall again.
- Ground EVERY claim in the retrieved text. If recall returns nothing relevant
  (no hits, or only low scores that don't actually answer the question), say you
  don't have that information rather than guessing.
- Be concise and factual. Give the specific score / fact asked for; do not pad.
"""


@dataclass
class AskDeps:
    """Dependencies threaded through one agent run."""

    runtime: KhoraRuntime
    trace: list[dict] = field(default_factory=list)


# Pin the Chat Completions provider explicitly: in pydantic-ai v2.0 a bare
# ``openai:`` prefix will switch to the Responses API, so use ``openai-chat:``
# to keep today's behavior.
agent: Agent[AskDeps, str] = Agent(
    model=f"openai-chat:{get_settings().answer_model}",
    deps_type=AskDeps,
    system_prompt=SYSTEM_PROMPT,
)


def _parse_dt(value: str) -> datetime:
    """Parse an ISO datetime or ``YYYY-MM-DD`` into a tz-aware UTC datetime."""
    text = value.strip()
    # ``fromisoformat`` handles both date and datetime forms (incl. a trailing Z
    # once normalized). Accept a bare date by letting it default to midnight.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@agent.tool
async def recall(
    ctx: RunContext[AskDeps],
    query: str,
    mode: str = "hybrid",
    limit: int = 10,
    source_type: str | None = None,
    occurred_after: str | None = None,
    occurred_before: str | None = None,
    min_similarity: float = 0.0,
) -> str:
    """Search the World Cup 2026 knowledge store for grounded context.

    Args:
        query: Natural-language search query.
        mode: Search mode: vector | graph | hybrid | all | keyword (default hybrid).
        limit: Max chunks to retrieve.
        source_type: Restrict to 'match' (scores/fixtures/standings) or 'news'
            (analysis/narrative). Omit to search everything.
        occurred_after: Only content occurring at/after this ISO date or datetime (UTC).
        occurred_before: Only content occurring strictly before this ISO date or datetime (UTC).
        min_similarity: Minimum vector similarity threshold (0.0 keeps all).

    Returns:
        A compact context string of the top hits, prefixed with a stats line the
        model can use to decide whether to refine the search or abstain.
    """
    search_mode = _MODE_MAP.get(mode.strip().lower(), SearchMode.HYBRID)

    # Build the temporal/source filter. occurred_after -> $gte, occurred_before -> $lt.
    occurred: dict = {}
    if occurred_after:
        occurred["$gte"] = _parse_dt(occurred_after)
    if occurred_before:
        occurred["$lt"] = _parse_dt(occurred_before)

    filter_dict: dict = {}
    if source_type:
        filter_dict["source_type"] = source_type.strip().lower()
    if occurred:
        filter_dict["occurred_at"] = occurred

    params = {
        "query": query,
        "mode": search_mode.name.lower(),
        "limit": limit,
        "source_type": source_type,
        "occurred_after": occurred_after,
        "occurred_before": occurred_before,
        "min_similarity": min_similarity,
    }

    start = perf_counter()
    result = await ctx.deps.runtime.recall(
        query,
        limit=limit,
        mode=search_mode,
        min_similarity=min_similarity,
        filter=filter_dict or None,
    )
    latency_ms = (perf_counter() - start) * 1000.0

    ctx.deps.trace.append(
        {
            "params": params,
            "latency_ms": latency_ms,
            "result": recall_result_to_dict(result),
        }
    )

    top_score = result.chunks[0].score if result.chunks else None
    max_raw = (result.engine_info or {}).get("max_raw_vector_score")
    header = (
        f"[recall hits={len(result.chunks)} top_score={top_score} "
        f"max_raw_vector_score={max_raw}]"
    )
    body = context_text(result, max_chunks=6)
    return f"{header}\n{body}" if body.strip() else f"{header}\n(no matching content)"


def _looks_abstained(answer: str, any_hits: bool) -> bool:
    """Heuristic: no hits at all, or the answer states it lacks the info."""
    if not any_hits:
        return True
    low = answer.lower()
    return any(marker in low for marker in _ABSTAIN_MARKERS)


async def answer_question(question: str) -> dict:
    """Run the answer agent for ``question`` and return the API response dict."""
    runtime = await get_runtime()
    deps = AskDeps(runtime=runtime)

    start = perf_counter()
    result = await agent.run(question, deps=deps)
    total_latency_ms = (perf_counter() - start) * 1000.0

    answer = result.output
    usage = result.usage()

    trace = deps.trace
    recall_latency_ms = sum(call["latency_ms"] for call in trace)
    chunks_returned = sum(len(call["result"]["chunks"]) for call in trace)

    # top_score = best chunk score seen across all recall calls.
    top_score: float | None = None
    max_raw_vector_score: float | None = None
    any_hits = False
    for call in trace:
        for chunk in call["result"]["chunks"]:
            any_hits = True
            score = chunk.get("score")
            if score is not None and (top_score is None or score > top_score):
                top_score = score
        raw = call["result"]["engine_info"].get("max_raw_vector_score")
        if raw is not None and (
            max_raw_vector_score is None or raw > max_raw_vector_score
        ):
            max_raw_vector_score = raw

    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0

    return {
        "question": question,
        "answer": answer,
        "abstained": _looks_abstained(answer, any_hits),
        "metrics": {
            "total_latency_ms": total_latency_ms,
            "recall_latency_ms": recall_latency_ms,
            "recall_calls": len(trace),
            "chunks_returned": chunks_returned,
            "top_score": top_score,
            "max_raw_vector_score": max_raw_vector_score,
            "answer_tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "total": input_tokens + output_tokens,
            },
        },
        "recall_trace": trace,
    }
