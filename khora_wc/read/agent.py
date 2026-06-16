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
from datetime import datetime, timedelta, timezone
from time import perf_counter

from pydantic_ai import Agent, RunContext

from khora import RecallResult, SearchMode

from khora_wc.config import get_settings
from khora_wc.read.serialize import recall_result_to_dict
from khora_wc.runtime import KhoraRuntime, get_runtime

logger = logging.getLogger(__name__)

# recall() always searches in hybrid mode with a fixed breadth; only the query
# and the (sanitized) filter vary per call.
_RECALL_MODE = SearchMode.HYBRID
_RECALL_LIMIT = 20
_RECALL_MIN_SIMILARITY = 0.0

# Generic co-occurrence edges khora auto-creates; surfaced after typed edges.
_GENERIC_RELS = {"CO_OCCURS_WITH", "ASSOCIATED_WITH"}

_ONE_DAY = timedelta(days=1)

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

Calling `recall`:
- `recall(query, filter=None)` is your ONLY tool. `query` is a natural-language
  search string; `filter` is an optional JSON object that scopes the search.
- The filter accepts exactly two keys (both optional):
  - "source_type": "match" or "news".
    Use "match" for anything factual: scores, results, fixtures, standings,
    who plays whom, top scorers. Use "news" for narrative, analysis,
    storylines, or opinion. Omit it when unsure.
  - "occurred_at": {"$gte": <ISO datetime>, "$lt": <ISO datetime>} — restrict to
    content occurring at/after $gte and strictly before $lt. Either bound may be
    omitted. Values are UTC ISO-8601 strings; a bare date "YYYY-MM-DD" is treated
    as midnight UTC.
  Example: recall("matches today", {"source_type": "match",
  "occurred_at": {"$gte": "2026-06-16", "$lt": "2026-06-17"}})

Rules:
- You MUST call `recall` at least once before answering. Never answer from prior
  knowledge or assumptions.
- Only set "occurred_at" when the QUESTION itself is time-scoped ("today",
  "tomorrow", "this week", "on June 14"); resolve such phrases relative to the
  current date (given below) into a range. A question about a specific match,
  team, or score (e.g. "the Mexico match") is NOT time-scoped — do NOT add an
  occurred_at filter for it; matches may have been played on any prior date.
- For broad / aggregate / list questions ("which teams won", "list all results so
  far", "who has qualified", "show every Group B match"), the answer is spread
  across MANY individual match documents (each says e.g. "Mexico 2, South Africa
  0. Mexico beat South Africa."). Set source_type="match", read the results out of
  every returned match, and synthesize. Do NOT abstain when match documents come
  back — only finished matches have a winner.
- If the first recall is weak or off-target, refine: rephrase the query, widen or
  drop the time window, or drop the source_type filter, then call recall again.
- Ground EVERY claim in the retrieved CONTENT. Decide whether to answer or abstain
  based on whether the returned text actually contains the answer — NOT on any
  notion of relevance or scores (you do not see scores). If the content does not
  answer the question, say you don't have that information rather than guessing.
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


@agent.system_prompt
def current_date_prompt() -> str:
    """Inject the live UTC date/time so time-scoped questions resolve correctly.

    Computed at run time (the env wall-clock is in June 2026), never hardcoded.
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    return (
        f"Today is {today.isoformat()} (UTC); the current time is "
        f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')}. Resolve time-scoped questions "
        "relative to this. For example, 'today' is occurred_at "
        f'{{"$gte": "{today.isoformat()}T00:00:00Z", '
        f'"$lt": "{(today + _ONE_DAY).isoformat()}T00:00:00Z"}}; "this week" and '
        '"tomorrow" similarly. Pass the resolved range in the recall filter\'s '
        "occurred_at only when the question is itself time-scoped."
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


def _sanitize_filter(raw: object) -> tuple[dict, dict]:
    """Sanitize an agent-supplied filter for khora.

    Accepts only the keys ``source_type`` and ``occurred_at``; ``occurred_at``
    bounds (``$gte`` / ``$lt``) arrive as ISO date/datetime strings and are parsed
    into tz-aware UTC datetimes. Anything malformed is dropped rather than raising,
    so a bad filter degrades to a broader (or unfiltered) search.

    Returns ``(khora_filter, recorded)`` where ``khora_filter`` is what we pass to
    khora (datetimes; ``{}`` if nothing valid) and ``recorded`` is the original,
    JSON-friendly view echoed into the trace.
    """
    if not isinstance(raw, dict):
        return {}, {}

    khora_filter: dict = {}
    recorded: dict = {}

    source_type = raw.get("source_type")
    if isinstance(source_type, str) and source_type.strip():
        normalized = source_type.strip().lower()
        khora_filter["source_type"] = normalized
        recorded["source_type"] = normalized

    occurred_at = raw.get("occurred_at")
    if isinstance(occurred_at, dict):
        bounds: dict = {}
        recorded_bounds: dict = {}
        for op in ("$gte", "$lt"):
            value = occurred_at.get(op)
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                bounds[op] = _parse_dt(value)
            except ValueError:
                logger.warning("recall: dropping unparseable %s bound %r", op, value)
                continue
            recorded_bounds[op] = value
        if bounds:
            khora_filter["occurred_at"] = bounds
            recorded["occurred_at"] = recorded_bounds

    return khora_filter, recorded


def _resolve_name(entity_id: object, id2name: dict[str, str]) -> str:
    """Resolve an entity id to its name, falling back to a short id."""
    key = str(entity_id) if entity_id is not None else ""
    name = id2name.get(key)
    if name:
        return name
    return key[:8] if key else "?"


def _format_context(result: RecallResult) -> str:
    """Render a RecallResult into agent-facing text with NO scores.

    Three labeled sections — CHUNKS (content + a source hint), ENTITIES
    (``name (TYPE)``), and RELATIONSHIPS (``src -[TYPE]-> dst`` with typed
    ontology edges before generic CO_OCCURS_WITH/ASSOCIATED_WITH). Scores are
    intentionally omitted; the model decides relevance from the content itself.
    """
    chunks = result.chunks or []
    entities = result.entities or []
    relationships = result.relationships or []

    id2name = {str(e.id): e.name for e in entities}
    doc_by_id = {str(d.id): d for d in (result.documents or [])}

    lines: list[str] = [f"Retrieved {len(chunks)} chunks."]

    lines.append("\nCHUNKS:")
    if chunks:
        for chunk in chunks:
            doc = doc_by_id.get(str(chunk.document_id))
            hints: list[str] = []
            if doc is not None and doc.title:
                hints.append(doc.title)
            if chunk.occurred_at is not None:
                hints.append(chunk.occurred_at.date().isoformat())
            hint = f" [{' | '.join(hints)}]" if hints else ""
            lines.append(f"- {chunk.content}{hint}")
    else:
        lines.append("(none)")

    lines.append("\nENTITIES:")
    if entities:
        for entity in entities:
            lines.append(f"- {entity.name} ({entity.entity_type})")
    else:
        lines.append("(none)")

    lines.append("\nRELATIONSHIPS:")
    if relationships:
        # Typed ontology edges first; generic co-occurrence edges last.
        ranked = sorted(
            relationships,
            key=lambda r: r.relationship_type in _GENERIC_RELS,
        )
        for rel in ranked:
            src = _resolve_name(rel.source_entity_id, id2name)
            dst = _resolve_name(rel.target_entity_id, id2name)
            lines.append(f"- {src} -[{rel.relationship_type}]-> {dst}")
    else:
        lines.append("(none)")

    return "\n".join(lines)


@agent.tool
async def recall(
    ctx: RunContext[AskDeps],
    query: str,
    filter: dict | None = None,
) -> str:
    """Search the World Cup 2026 knowledge store for grounded context.

    Args:
        query: Natural-language search query.
        filter: Optional JSON object scoping the search. Accepts exactly two
            optional keys:
              - "source_type": "match" (scores/fixtures/standings/top scorers) or
                "news" (analysis/narrative). Omit to search everything.
              - "occurred_at": {"$gte": <ISO date/datetime>, "$lt": <ISO
                date/datetime>} — restrict to content occurring at/after $gte and
                strictly before $lt (UTC; a bare "YYYY-MM-DD" means midnight UTC).
                Either bound may be omitted.
            Example: {"source_type": "match",
            "occurred_at": {"$gte": "2026-06-16", "$lt": "2026-06-17"}}.

    Returns:
        Context text with CHUNKS, ENTITIES, and RELATIONSHIPS sections. Decide
        whether the content answers the question; there are no relevance scores.
    """
    khora_filter, recorded_filter = _sanitize_filter(filter)
    occurred_recorded = recorded_filter.get("occurred_at", {})

    params = {
        "query": query,
        "mode": _RECALL_MODE.name.lower(),
        "limit": _RECALL_LIMIT,
        "source_type": recorded_filter.get("source_type"),
        "occurred_after": occurred_recorded.get("$gte"),
        "occurred_before": occurred_recorded.get("$lt"),
        "min_similarity": _RECALL_MIN_SIMILARITY,
        "filter": recorded_filter or None,
    }

    start = perf_counter()
    result = await ctx.deps.runtime.recall(
        query,
        limit=_RECALL_LIMIT,
        mode=_RECALL_MODE,
        min_similarity=_RECALL_MIN_SIMILARITY,
        filter=khora_filter or None,
    )
    latency_ms = (perf_counter() - start) * 1000.0

    # The full scored result still goes to the trace (UI/metrics need scores);
    # only the string returned to the model strips relevance.
    ctx.deps.trace.append(
        {
            "params": params,
            "latency_ms": latency_ms,
            "result": recall_result_to_dict(result),
        }
    )

    return _format_context(result)


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
