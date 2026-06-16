interface Props {
  message: string;
  onRetry?: () => void;
}

// A genuine failure (network/5xx) — distinct from abstention, which is calm.
export function ErrorCard({ message, onRetry }: Props) {
  return (
    <div
      className="animate-fade-up rounded-2xl border border-red-500/40 bg-red-950/30 p-5"
      role="alert"
    >
      <div className="flex items-center gap-2">
        <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5 text-red-300" aria-hidden="true">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
          <path d="M12 7.5v5.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          <circle cx="12" cy="16.5" r="1.1" fill="currentColor" />
        </svg>
        <span className="font-semibold text-red-200">Something went wrong</span>
      </div>
      <p className="mt-2 text-sm text-red-200/90">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-lg border border-red-400/40 bg-red-500/10 px-3 py-1.5 text-sm font-medium text-red-100 transition hover:bg-red-500/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400/40"
        >
          Try again
        </button>
      )}
    </div>
  );
}
