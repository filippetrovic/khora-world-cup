// Mirrors the backend response contract for POST /ask, GET /api/stats.
// Kept deliberately permissive (nullable scores, optional fields) because the
// khora recall trace can omit values depending on which query the agent runs.

export interface AnswerTokens {
  input: number;
  output: number;
  total: number;
}

export interface Metrics {
  total_latency_ms: number;
  recall_latency_ms: number;
  recall_calls: number;
  chunks_returned: number;
  top_score: number | null;
  max_raw_vector_score: number | null;
  answer_tokens: AnswerTokens;
}

export interface RecallParams {
  query: string;
  mode: string | null;
  limit: number | null;
  source_type: string | null;
  occurred_after: string | null;
  occurred_before: string | null;
  min_similarity: number | null;
}

export interface Chunk {
  content: string;
  score: number | null;
  document_id: string | null;
  occurred_at: string | null;
}

export interface Entity {
  name: string;
  entity_type: string | null;
  description: string | null;
  score: number | null;
}

export interface Relationship {
  relationship_type: string | null;
  source_entity_id: string | null;
  target_entity_id: string | null;
  // Server-resolved endpoint names (may be null if unresolved). Preferred over
  // the *_entity_id fields for display.
  source_name?: string | null;
  target_name?: string | null;
  description: string | null;
  score: number | null;
}

export interface SourceDocument {
  // khora's internal document id — the FK target of Chunk.document_id, used to
  // link a chunk back to its source document in the UI.
  id: string | null;
  title: string | null;
  source_type: string | null;
  source_name: string | null;
  source_url: string | null;
  source_timestamp: string | null;
  external_id: string | null;
}

export interface RecallResult {
  chunks: Chunk[];
  entities: Entity[];
  relationships: Relationship[];
  documents: SourceDocument[];
  engine_info: { max_raw_vector_score?: number | null } & Record<string, unknown>;
}

export interface RecallCall {
  params: RecallParams;
  latency_ms: number;
  result: RecallResult;
}

export interface AskResponse {
  question: string;
  answer: string;
  abstained: boolean;
  metrics: Metrics;
  recall_trace: RecallCall[];
}

export interface Stats {
  documents: number;
  entities: number;
  relationships: number;
  last_activity_at: string | null;
  chunks: number;
}
