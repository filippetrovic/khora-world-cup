import type { Metrics } from "../lib/types";
import { fmtMs, fmtScore, fmtInt } from "../lib/format";

interface Props {
  metrics: Metrics;
}

interface Item {
  label: string;
  value: string;
  title: string;
  tone?: "default" | "accent";
}

// Glanceable badges summarising the retrieval + answer cost. Each carries a
// `title` so hovering explains what the number means.
export function MetricsStrip({ metrics }: Props) {
  const items: Item[] = [
    {
      label: "recall",
      value: fmtMs(metrics.recall_latency_ms),
      title: "Time spent inside khora recall calls (metrics.recall_latency_ms)",
    },
    {
      label: "total",
      value: fmtMs(metrics.total_latency_ms),
      title: "End-to-end time including the LLM (metrics.total_latency_ms)",
      tone: "accent",
    },
    {
      label: "recall calls",
      value: fmtInt(metrics.recall_calls),
      title: "How many times the agent queried khora (metrics.recall_calls)",
    },
    {
      label: "top score",
      value: fmtScore(metrics.top_score),
      title: "Best chunk relevance score seen, 0–1 (metrics.top_score)",
    },
    {
      label: "confidence",
      value: fmtScore(metrics.max_raw_vector_score),
      title: "Max raw vector similarity from the engine (metrics.max_raw_vector_score)",
    },
    {
      label: "tokens",
      value: fmtInt(metrics.answer_tokens?.total),
      title: `Answer tokens — in ${fmtInt(metrics.answer_tokens?.input)} / out ${fmtInt(
        metrics.answer_tokens?.output,
      )} (metrics.answer_tokens.total)`,
    },
  ];

  return (
    <div className="flex flex-wrap gap-2" aria-label="response metrics">
      {items.map((it) => (
        <div
          key={it.label}
          title={it.title}
          className={`flex cursor-default items-baseline gap-1.5 rounded-lg border px-3 py-1.5 transition ${
            it.tone === "accent"
              ? "border-gold-500/30 bg-gold-500/10"
              : "border-pitch-800/70 bg-pitch-900/50"
          }`}
        >
          <span className="text-[10px] uppercase tracking-wide text-pitch-400/90">
            {it.label}
          </span>
          <span
            className={`font-mono text-sm font-semibold tabular-nums ${
              it.tone === "accent" ? "text-gold-200" : "text-pitch-100"
            }`}
          >
            {it.value}
          </span>
        </div>
      ))}
    </div>
  );
}
