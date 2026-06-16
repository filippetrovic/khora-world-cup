import type { AskResponse } from "../lib/types";
import { MetricsStrip } from "./MetricsStrip";

interface Props {
  data: AskResponse;
}

// The headline result. Abstention is a calm, distinct state — not an error.
export function AnswerCard({ data }: Props) {
  const abstained = data.abstained;

  return (
    <section
      className={`animate-fade-up overflow-hidden rounded-2xl border backdrop-blur ${
        abstained
          ? "border-pitch-700/60 bg-pitch-900/40"
          : "border-pitch-600/40 bg-gradient-to-br from-pitch-800/50 to-pitch-900/60 shadow-2xl shadow-black/30"
      }`}
      aria-live="polite"
    >
      <div className="p-6 sm:p-7">
        <div className="mb-3 flex items-center gap-2">
          {abstained ? (
            <>
              <InfoIcon className="h-4 w-4 text-pitch-400" />
              <span className="text-xs font-medium uppercase tracking-wide text-pitch-400">
                No grounded answer
              </span>
            </>
          ) : (
            <>
              <CheckIcon className="h-4 w-4 text-gold-300" />
              <span className="text-xs font-medium uppercase tracking-wide text-gold-300/90">
                Answer
              </span>
            </>
          )}
        </div>

        <p className="mb-1 text-sm text-pitch-400/80">{data.question}</p>

        {abstained ? (
          <div className="mt-2">
            <p className="text-lg font-medium text-pitch-100">
              khora didn't find that in its sources.
            </p>
            <p className="mt-2 text-sm leading-relaxed text-pitch-300/90">{data.answer}</p>
            <p className="mt-3 text-xs text-pitch-400/70">
              Try a question about a specific match, group, top scorers, or recent news.
            </p>
          </div>
        ) : (
          <p className="whitespace-pre-line text-lg leading-relaxed text-pitch-50 sm:text-xl">
            {data.answer}
          </p>
        )}
      </div>

      <div className="border-t border-pitch-800/60 bg-pitch-950/30 px-6 py-4 sm:px-7">
        <MetricsStrip metrics={data.metrics} />
      </div>
    </section>
  );
}

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="m5 13 4 4L19 7"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function InfoIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 11v5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="12" cy="7.6" r="1.1" fill="currentColor" />
    </svg>
  );
}
