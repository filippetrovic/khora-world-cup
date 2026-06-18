import { useLayoutEffect, useRef } from "react";

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  loading: boolean;
  disabled?: boolean;
}

const EXAMPLES = [
  "What was the score in the Mexico match?",
  "Who are the tournament's top scorers?",
  "What's happening in Group A?",
  "Latest World Cup news",
];

// The single, prominent question field + seeded example chips.
export function QuestionBox({ value, onChange, onSubmit, loading, disabled }: Props) {
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Grow the textarea to fit its content (height only — width is fixed by the
  // flex layout). Reset to "auto" first so it also shrinks when text is deleted.
  // Runs on every value change, so programmatic sets (example chips, clear on
  // submit) resize too. A max-height in the className caps it and scrolls beyond.
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSubmit();
    }
  }

  function pick(q: string) {
    onChange(q);
    // Submit immediately when an example is clicked; let state settle first.
    requestAnimationFrame(() => onSubmit());
  }

  return (
    <div className="w-full">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
        className="group relative"
      >
        <label htmlFor="question" className="sr-only">
          Ask a question about the World Cup 2026
        </label>
        <div className="flex items-center gap-2 rounded-2xl border border-pitch-700/70 bg-pitch-900/70 p-2 pl-5 shadow-2xl shadow-black/30 backdrop-blur transition focus-within:border-gold-400/60 focus-within:ring-2 focus-within:ring-gold-400/30">
          <SearchIcon className="h-5 w-5 shrink-0 text-pitch-400" />
          <textarea
            id="question"
            ref={inputRef}
            rows={1}
            autoComplete="off"
            placeholder="Ask anything about the World Cup 2026…"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            className="block max-h-48 min-w-0 flex-1 resize-none overflow-y-auto bg-transparent py-2.5 text-base leading-relaxed text-pitch-50 placeholder:text-pitch-400/70 focus:outline-none disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={loading || disabled || value.trim().length === 0}
            className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-gradient-to-br from-gold-400 to-gold-500 px-4 py-2.5 text-sm font-semibold text-pitch-950 shadow-lg shadow-gold-500/20 transition hover:from-gold-300 hover:to-gold-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-gold-300 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? (
              <>
                <Spinner /> Asking
              </>
            ) : (
              <>
                Ask <ArrowIcon className="h-4 w-4" />
              </>
            )}
          </button>
        </div>
      </form>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-xs text-pitch-400/80">Try:</span>
        {EXAMPLES.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => pick(q)}
            disabled={loading || disabled}
            className="rounded-full border border-pitch-700/60 bg-pitch-900/40 px-3 py-1 text-xs text-pitch-200 transition hover:border-gold-400/50 hover:bg-pitch-800/60 hover:text-gold-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-gold-400/40 disabled:opacity-50"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

function SearchIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="1.8" />
      <path d="m20 20-3.2-3.2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function ArrowIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M5 12h14m0 0-6-6m6 6-6 6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
