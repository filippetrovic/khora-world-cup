import type { ReactNode } from "react";

type Tone = "neutral" | "green" | "gold" | "muted";

const toneClasses: Record<Tone, string> = {
  neutral: "bg-pitch-800/70 text-pitch-100 ring-pitch-700/60",
  green: "bg-pitch-600/25 text-pitch-100 ring-pitch-500/40",
  gold: "bg-gold-500/15 text-gold-200 ring-gold-500/30",
  muted: "bg-pitch-900/60 text-pitch-300/80 ring-pitch-800/60",
};

interface Props {
  children: ReactNode;
  tone?: Tone;
  title?: string;
  className?: string;
}

// Compact rounded label used for params, metrics, entity tags, etc.
export function Chip({ children, tone = "neutral", title, className }: Props) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${toneClasses[tone]} ${className ?? ""}`}
    >
      {children}
    </span>
  );
}
