import { fmtScore, scorePct } from "../lib/format";

interface Props {
  score: number | null | undefined;
  label?: string;
  className?: string;
}

// A 0..1 score rendered as a thin animated bar with the numeric value beside it.
export function ScoreBar({ score, label, className }: Props) {
  const pct = scorePct(score);
  return (
    <div className={`flex items-center gap-2 ${className ?? ""}`}>
      <div
        className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-pitch-800/80"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={score ?? 0}
        aria-label={label ?? "score"}
      >
        <div
          className="absolute inset-y-0 left-0 origin-left animate-grow-x rounded-full bg-gradient-to-r from-pitch-500 to-gold-400"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-12 shrink-0 text-right font-mono text-[11px] tabular-nums text-pitch-200/90">
        {fmtScore(score)}
      </span>
    </div>
  );
}
