import type { AskResponse, Stats } from "./types";

// Same-origin calls. In production the FastAPI app serves this bundle at "/", so
// /ask and /api/* resolve directly. In dev, vite proxies them to :8000.

export async function ask(
  question: string,
  signal?: AbortSignal,
): Promise<AskResponse> {
  const res = await fetch("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body?.detail ? `: ${JSON.stringify(body.detail)}` : "";
    } catch {
      // ignore non-JSON error bodies
    }
    throw new Error(`Request failed (${res.status})${detail}`);
  }
  return (await res.json()) as AskResponse;
}

export async function fetchStats(signal?: AbortSignal): Promise<Stats> {
  const res = await fetch("/api/stats", { signal });
  if (!res.ok) throw new Error(`Stats failed (${res.status})`);
  return (await res.json()) as Stats;
}
