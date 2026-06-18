import { useState } from "react";
import type { RecallCall, RecallParams, SourceDocument } from "../lib/types";
import { Chip } from "./Chip";
import { ScoreBar } from "./ScoreBar";
import { fmtMs, fmtDateTime, fmtDate, shortId } from "../lib/format";

interface Props {
  trace: RecallCall[];
}

// The expandable "What khora returned" panel — collapsed by default. This is the
// debug/teaching surface that showcases the pydantic-ai agent choosing how to
// query khora, then the chunks/entities/relationships/documents it got back.
export function RecallTrace({ trace }: Props) {
  const [open, setOpen] = useState(false);

  const totalChunks = trace.reduce((n, c) => n + (c.result?.chunks?.length ?? 0), 0);
  const totalDocs = trace.reduce((n, c) => n + (c.result?.documents?.length ?? 0), 0);

  if (trace.length === 0) return null;

  return (
    <section className="animate-fade-up rounded-2xl border border-pitch-800/70 bg-pitch-900/40 backdrop-blur">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 rounded-2xl px-5 py-4 text-left transition hover:bg-pitch-800/30 focus:outline-none focus-visible:ring-2 focus-visible:ring-gold-400/40"
      >
        <span className="flex items-center gap-2">
          <GraphIcon className="h-4 w-4 text-gold-300" />
          <span className="font-semibold text-pitch-50">What khora returned</span>
          <span className="text-xs text-pitch-400/80">
            {trace.length} recall call{trace.length === 1 ? "" : "s"} · {totalChunks} chunks ·{" "}
            {totalDocs} docs
          </span>
        </span>
        <ChevronIcon
          className={`h-5 w-5 shrink-0 text-pitch-400 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div className="space-y-4 px-5 pb-5">
          <p className="text-xs leading-relaxed text-pitch-400/80">
            Each block below is one recall the{" "}
            <span className="text-gold-200">pydantic-ai agent</span> chose to run against khora —
            the agent picks the mode, filters and limits itself.
          </p>
          {trace.map((call, i) => (
            <CallBlock key={i} call={call} index={i} />
          ))}
        </div>
      )}
    </section>
  );
}

function CallBlock({ call, index }: { call: RecallCall; index: number }) {
  // Each recall call collapses independently, just like the parent "What khora
  // returned" panel. Collapsed by default to keep multi-call traces scannable.
  const [open, setOpen] = useState(false);

  const r = call.result ?? {
    chunks: [],
    entities: [],
    relationships: [],
    documents: [],
    engine_info: {},
  };
  // Resolve a relationship endpoint to a display name. Prefer the server-
  // resolved name when present; otherwise fall back to an id→name map built from
  // this result's entities (usually empty — entities expose only names), and
  // finally to a shortened id stub.
  const nameById = new Map<string, string>();
  for (const e of r.entities ?? []) {
    const anyE = e as unknown as { id?: string; entity_id?: string };
    const id = anyE.id ?? anyE.entity_id;
    if (id) nameById.set(id, e.name);
  }
  const resolveEndpoint = (name: string | null | undefined, id: string | null) =>
    name || (id && nameById.get(id)) || shortId(id);

  // chunk.document_id is a foreign key into this call's documents list, so build
  // an id→document map to render each chunk's source as a clickable doc name.
  const docById = new Map<string, SourceDocument>();
  for (const d of r.documents ?? []) {
    if (d.id) docById.set(d.id, d);
  }

  const nChunks = r.chunks?.length ?? 0;
  const nEntities = r.entities?.length ?? 0;
  const nRels = r.relationships?.length ?? 0;
  const nDocs = r.documents?.length ?? 0;

  return (
    <div className="overflow-hidden rounded-xl border border-pitch-800/60 bg-pitch-950/40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={`flex w-full items-center justify-between gap-2 bg-pitch-900/40 px-4 py-2.5 text-left transition hover:bg-pitch-900/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-gold-400/40 ${
          open ? "border-b border-pitch-800/60" : ""
        }`}
      >
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs font-semibold text-pitch-200">
          <span className="flex h-5 w-5 items-center justify-center rounded-full bg-gold-500/20 text-[11px] text-gold-200">
            {index + 1}
          </span>
          recall call
          <span className="font-normal text-pitch-400/80">
            {nChunks} chunks · {nEntities} entities · {nRels} rels · {nDocs} docs
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <Chip tone="muted" title="Latency of this single recall call">
            {fmtMs(call.latency_ms)}
          </Chip>
          <ChevronIcon
            className={`h-4 w-4 shrink-0 text-pitch-400 transition-transform ${open ? "rotate-180" : ""}`}
          />
        </span>
      </button>

      {open && (
      <div className="space-y-4 p-4">
        <ParamRow params={call.params} />

        <Subsection label="Chunks" count={r.chunks?.length}>
          {(r.chunks ?? []).length === 0 ? (
            <Empty>No chunks returned.</Empty>
          ) : (
            <ul className="space-y-2.5">
              {r.chunks.map((c, ci) => {
                const doc = c.document_id ? docById.get(c.document_id) : undefined;
                return (
                  <li
                    key={ci}
                    className="rounded-lg border border-pitch-800/60 bg-pitch-900/40 p-3"
                  >
                    <p className="text-sm leading-relaxed text-pitch-100">{c.content}</p>
                    <div className="mt-2 flex items-center gap-3">
                      <ScoreBar score={c.score} label="chunk score" className="flex-1" />
                      {c.occurred_at && (
                        <span className="shrink-0 text-[11px] text-pitch-400/80" title="occurred_at">
                          {fmtDateTime(c.occurred_at)}
                        </span>
                      )}
                    </div>
                    {c.document_id && (
                      <div className="mt-1.5 text-[11px]">
                        {doc ? (
                          <span className="inline-flex items-center gap-1 text-pitch-400/80">
                            <span className="text-pitch-500/80">doc:</span>
                            <DocTitleLink doc={doc} />
                          </span>
                        ) : (
                          <span
                            className="font-mono text-[10px] text-pitch-500/80"
                            title={`document_id: ${c.document_id}`}
                          >
                            doc {shortId(c.document_id)}
                          </span>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </Subsection>

        <Subsection label="Entities" count={r.entities?.length}>
          {(r.entities ?? []).length === 0 ? (
            <Empty>No entities.</Empty>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {r.entities.map((e, ei) => (
                <Chip
                  key={ei}
                  tone="green"
                  title={e.description ?? undefined}
                >
                  <span className="font-semibold">{e.name}</span>
                  {e.entity_type && (
                    <span className="text-pitch-300/80">· {e.entity_type}</span>
                  )}
                  {typeof e.score === "number" && (
                    <span className="font-mono text-[10px] text-gold-200/90">
                      {e.score.toFixed(2)}
                    </span>
                  )}
                </Chip>
              ))}
            </div>
          )}
        </Subsection>

        <Subsection label="Relationships" count={r.relationships?.length}>
          {(r.relationships ?? []).length === 0 ? (
            <Empty>No relationships.</Empty>
          ) : (
            <ul className="space-y-2">
              {r.relationships.map((rel, ri) => (
                <li
                  key={ri}
                  className="rounded-lg border border-pitch-800/60 bg-pitch-900/40 px-3 py-2"
                >
                  <div className="flex flex-wrap items-center gap-1.5 text-sm">
                    <span className="font-medium text-pitch-100">
                      {resolveEndpoint(rel.source_name, rel.source_entity_id)}
                    </span>
                    <RelArrow label={rel.relationship_type ?? "related"} />
                    <span className="font-medium text-pitch-100">
                      {resolveEndpoint(rel.target_name, rel.target_entity_id)}
                    </span>
                  </div>
                  {rel.description && (
                    <p className="mt-1 text-xs leading-relaxed text-pitch-300/90">
                      {rel.description}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Subsection>

        <Subsection label="Source documents" count={r.documents?.length}>
          {(r.documents ?? []).length === 0 ? (
            <Empty>No documents.</Empty>
          ) : (
            <ul className="space-y-2">
              {r.documents.map((d, di) => (
                <li
                  key={di}
                  className="rounded-lg border border-pitch-800/60 bg-pitch-900/40 px-3 py-2.5"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <DocTitleLink doc={d} />
                      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-pitch-400/80">
                        {d.source_name && <span>{d.source_name}</span>}
                        {d.source_type && (
                          <>
                            <span aria-hidden="true">·</span>
                            <span>{d.source_type}</span>
                          </>
                        )}
                        {d.source_timestamp && (
                          <>
                            <span aria-hidden="true">·</span>
                            <span>{fmtDate(d.source_timestamp)}</span>
                          </>
                        )}
                      </div>
                      {d.id && (
                        <div
                          className="mt-1.5 font-mono text-[10px] text-pitch-500/80 break-all"
                          title="khora document id"
                        >
                          id {d.id}
                        </div>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Subsection>
      </div>
      )}
    </div>
  );
}

// A document's title rendered as a link to its source_url (with an external-link
// glyph), or plain text when no url. Shared by the Chunks footer and the Source
// documents list so a chunk's source reads identically to the documents list.
function DocTitleLink({ doc }: { doc: SourceDocument }) {
  const label = doc.title ?? doc.source_url ?? "Untitled source";
  if (doc.source_url) {
    return (
      <a
        href={doc.source_url}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 font-medium text-gold-200 hover:text-gold-100 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-gold-400/40"
      >
        {label}
        <ExternalIcon className="h-3 w-3 shrink-0" />
      </a>
    );
  }
  return <span className="font-medium text-pitch-100">{label}</span>;
}

// The row of param badges showing exactly how the agent queried khora.
function ParamRow({ params }: { params: RecallParams }) {
  if (!params) return null;
  return (
    <div>
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-pitch-400/80">
        Agent-chosen recall params
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {params.query && (
          <Chip tone="gold" title="The query string the agent searched with">
            “{params.query}”
          </Chip>
        )}
        {params.mode && <Param k="mode" v={params.mode} />}
        {typeof params.limit === "number" && <Param k="limit" v={String(params.limit)} />}
        {params.source_type && <Param k="source_type" v={params.source_type} />}
        {params.occurred_after && <Param k="after" v={fmtDate(params.occurred_after)} />}
        {params.occurred_before && <Param k="before" v={fmtDate(params.occurred_before)} />}
        {typeof params.min_similarity === "number" && (
          <Param k="min_sim" v={params.min_similarity.toFixed(2)} />
        )}
      </div>
    </div>
  );
}

function Param({ k, v }: { k: string; v: string }) {
  return (
    <Chip tone="neutral" title={`recall param: ${k}`}>
      <span className="text-pitch-400/90">{k}</span>
      <span className="font-mono text-pitch-100">{v}</span>
    </Chip>
  );
}

function Subsection({
  label,
  count,
  children,
}: {
  label: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-pitch-400/80">
        {label}
        {typeof count === "number" && (
          <span className="rounded-full bg-pitch-800/70 px-1.5 py-0.5 font-mono text-[10px] text-pitch-300">
            {count}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-xs italic text-pitch-500/80">{children}</p>;
}

function RelArrow({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-pitch-400">
      <span className="text-pitch-600">—[</span>
      <span className="font-mono text-[11px] font-semibold text-gold-200">{label}</span>
      <span className="text-pitch-600">]→</span>
    </span>
  );
}

function GraphIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <circle cx="6" cy="6" r="2.4" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="18" cy="8" r="2.4" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="9" cy="18" r="2.4" stroke="currentColor" strokeWidth="1.6" />
      <path d="m8 7.2 8 .6M7.4 8 8.6 15.8" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

function ChevronIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path d="m6 9 6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ExternalIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M14 5h5v5M19 5l-8 8M11 5H6a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
