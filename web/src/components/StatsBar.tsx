import type { Stats } from "../lib/types";
import { fmtInt } from "../lib/format";

interface Props {
  stats: Stats | null;
  statsError: boolean;
}

// Subtle store-size readout. Deliberately omits `chunks` (always 0 — khora quirk).
export function StatsBar({ stats, statsError }: Props) {
  if (statsError) {
    return (
      <div className="text-xs text-pitch-400/70" title="Could not load store stats">
        store offline
      </div>
    );
  }
  return (
    <div className="flex items-center gap-4 rounded-xl border border-pitch-800/70 bg-pitch-900/40 px-4 py-2 backdrop-blur">
      <Stat label="docs" value={stats?.documents} />
      <Divider />
      <Stat label="entities" value={stats?.entities} />
      <Divider />
      <Stat label="relationships" value={stats?.relationships} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value?: number }) {
  return (
    <div className="flex flex-col items-center leading-none" title={`${label} in the khora store`}>
      <span className="font-mono text-sm font-semibold tabular-nums text-gold-200">
        {value === undefined ? "—" : fmtInt(value)}
      </span>
      <span className="mt-0.5 text-[10px] uppercase tracking-wide text-pitch-400/80">
        {label}
      </span>
    </div>
  );
}

function Divider() {
  return <span className="h-7 w-px bg-pitch-800/70" aria-hidden="true" />;
}
