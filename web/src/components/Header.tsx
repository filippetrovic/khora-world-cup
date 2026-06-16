import type { Stats } from "../lib/types";
import { StatsBar } from "./StatsBar";

interface Props {
  stats: Stats | null;
  statsError: boolean;
}

// App identity + a subtle live store-size readout on the right.
export function Header({ stats, statsError }: Props) {
  return (
    <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-pitch-600/30 ring-1 ring-inset ring-pitch-500/40">
          <BallIcon className="h-6 w-6 text-gold-300" />
        </div>
        <div>
          <h1 className="text-xl font-extrabold tracking-tight text-pitch-50 sm:text-2xl">
            Khora <span className="text-pitch-400">·</span>{" "}
            <span className="text-gold-300">World Cup 2026</span>
          </h1>
          <p className="text-sm text-pitch-300/90">
            Ask anything about the tournament — grounded answers, not guesses.
          </p>
        </div>
      </div>
      <StatsBar stats={stats} statsError={statsError} />
    </header>
  );
}

function BallIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M12 7.2l3.1 2.25-1.18 3.65h-3.84L8.9 9.45 12 7.2z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M12 3.2v4M4.6 9.4l3.5 2.6M19.4 9.4l-3.5 2.6M7 19.5l1.9-3.2M17 19.5l-1.9-3.2"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </svg>
  );
}
