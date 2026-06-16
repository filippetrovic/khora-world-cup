// Inviting first-load state shown before any question is asked.
export function EmptyState() {
  return (
    <div className="animate-fade-in rounded-2xl border border-dashed border-pitch-800/70 bg-pitch-900/20 px-6 py-12 text-center">
      <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-pitch-800/50 ring-1 ring-inset ring-pitch-700/60">
        <svg viewBox="0 0 24 24" fill="none" className="h-7 w-7 text-gold-300" aria-hidden="true">
          <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="1.8" />
          <path d="m20 20-3.2-3.2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </div>
      <h2 className="text-lg font-semibold text-pitch-100">Ask the World Cup anything.</h2>
      <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-pitch-300/90">
        Scores, fixtures, group standings, top scorers, and the latest news — every answer is
        retrieved and grounded by khora, then shown with the full evidence trail.
      </p>
      <p className="mt-4 text-xs text-pitch-400/80">
        Pick an example above, or type your own question.
      </p>
    </div>
  );
}
