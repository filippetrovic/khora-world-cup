import { useEffect, useState } from "react";

const HINTS = [
  "searching khora…",
  "ranking matches…",
  "walking the knowledge graph…",
  "grounding the answer…",
];

// Tasteful waiting state. Warm calls are 1–4s but ingestion can push past 10s,
// so we cycle reassuring hints and animate a shimmer rather than a bare spinner.
export function LoadingCard() {
  const [hint, setHint] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setHint((h) => (h + 1) % HINTS.length), 1600);
    return () => clearInterval(id);
  }, []);

  return (
    <div
      className="animate-fade-up rounded-2xl border border-pitch-800/70 bg-pitch-900/50 p-6 backdrop-blur"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-3">
        <Spinner />
        <span className="text-sm font-medium text-pitch-200 transition-all duration-300">
          {HINTS[hint]}
        </span>
      </div>
      <div className="mt-5 space-y-3">
        <Skeleton className="w-[92%]" />
        <Skeleton className="w-[78%]" />
        <Skeleton className="w-[85%]" />
        <Skeleton className="w-[40%]" />
      </div>
      <span className="sr-only">Loading answer…</span>
    </div>
  );
}

function Skeleton({ className }: { className?: string }) {
  return (
    <div className={`relative h-3.5 overflow-hidden rounded bg-pitch-800/70 ${className ?? ""}`}>
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-pitch-700/40 to-transparent" />
    </div>
  );
}

function Spinner() {
  return (
    <svg className="h-5 w-5 animate-spin text-gold-300" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
