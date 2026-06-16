import { useEffect, useRef, useState } from "react";
import type { AskResponse, Stats } from "./lib/types";
import { ask as askApi, fetchStats } from "./lib/api";
import { MOCK_ANSWER, MOCK_ABSTAINED, MOCK_STATS } from "./lib/mock";
import { Header } from "./components/Header";
import { QuestionBox } from "./components/QuestionBox";
import { LoadingCard } from "./components/LoadingCard";
import { AnswerCard } from "./components/AnswerCard";
import { RecallTrace } from "./components/RecallTrace";
import { EmptyState } from "./components/EmptyState";
import { ErrorCard } from "./components/ErrorCard";
import { Footer } from "./components/Footer";

// Set VITE_USE_MOCK=1 to develop fully offline against the local fixtures.
const USE_MOCK = import.meta.env.VITE_USE_MOCK === "1";

type Status = "idle" | "loading" | "done" | "error";

export default function App() {
  const [question, setQuestion] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string>("");
  const [stats, setStats] = useState<Stats | null>(null);
  const [statsError, setStatsError] = useState(false);

  const lastQuestion = useRef("");
  const abortRef = useRef<AbortController | null>(null);

  // Load store stats once on mount (subtle, non-blocking).
  useEffect(() => {
    const ac = new AbortController();
    if (USE_MOCK) {
      setStats(MOCK_STATS);
      return;
    }
    fetchStats(ac.signal)
      .then(setStats)
      .catch(() => setStatsError(true));
    return () => ac.abort();
  }, []);

  async function submit() {
    const q = question.trim();
    if (!q || status === "loading") return;

    // Re-asking clears the previous result (no chat thread).
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    lastQuestion.current = q;

    setStatus("loading");
    setResult(null);
    setError("");

    try {
      const data = await mockableAsk(q, ac.signal);
      if (ac.signal.aborted) return;
      setResult(data);
      setStatus("done");
    } catch (err) {
      if (ac.signal.aborted) return;
      setError(err instanceof Error ? err.message : "Unknown error");
      setStatus("error");
    }
  }

  return (
    <div className="app-bg min-h-full">
      <div className="pitch-stripes min-h-full">
        <div className="mx-auto flex min-h-full max-w-3xl flex-col px-4 py-8 sm:px-6 sm:py-12">
          <Header stats={stats} statsError={statsError} />

          <main className="mt-8 flex-1">
            <QuestionBox
              value={question}
              onChange={setQuestion}
              onSubmit={submit}
              loading={status === "loading"}
            />

            <div className="mt-6 space-y-5">
              {status === "idle" && <EmptyState />}
              {status === "loading" && <LoadingCard />}
              {status === "error" && (
                <ErrorCard
                  message={error}
                  onRetry={() => {
                    setQuestion(lastQuestion.current);
                    // submit() reads from state; restore then fire.
                    requestAnimationFrame(submit);
                  }}
                />
              )}
              {status === "done" && result && (
                <>
                  <AnswerCard data={result} />
                  <RecallTrace trace={result.recall_trace} />
                </>
              )}
            </div>
          </main>

          <Footer />
        </div>
      </div>
    </div>
  );
}

// Routes through the real API, or returns a fixture under VITE_USE_MOCK with a
// short simulated delay so loading/abstention states are exercisable offline.
async function mockableAsk(q: string, signal: AbortSignal): Promise<AskResponse> {
  if (!USE_MOCK) return askApi(q, signal);
  await new Promise((r) => setTimeout(r, 1100));
  const abstain = /2030|moon|stock|weather/i.test(q);
  const base = abstain ? MOCK_ABSTAINED : MOCK_ANSWER;
  return { ...base, question: q };
}
