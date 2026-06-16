import type { AskResponse, Stats } from "./types";

// Local fixtures matching the backend contract VERBATIM, used to develop and
// verify every UI state offline. Toggled on with VITE_USE_MOCK=1 (see api usage
// in App during dev) — never bundled behavior in production calls.

export const MOCK_STATS: Stats = {
  documents: 312,
  entities: 1487,
  relationships: 2056,
  last_activity_at: "2026-06-16T09:12:00+00:00",
  chunks: 0,
};

export const MOCK_ANSWER: AskResponse = {
  question: "What was the score in the Mexico match?",
  answer:
    "Mexico beat Canada 2–1 in their Group A fixture at Estadio Azteca on June 11, 2026. " +
    "Hirving Lozano opened the scoring in the 23rd minute, Canada equalised through Jonathan David " +
    "just before half-time, and Santiago Giménez scored the winner in the 78th minute.",
  abstained: false,
  metrics: {
    total_latency_ms: 1840.5,
    recall_latency_ms: 612.3,
    recall_calls: 2,
    chunks_returned: 7,
    top_score: 0.873,
    max_raw_vector_score: 0.441,
    answer_tokens: { input: 1842, output: 96, total: 1938 },
  },
  recall_trace: [
    {
      params: {
        query: "Mexico vs Canada match score Group A",
        mode: "hybrid",
        limit: 10,
        source_type: "match",
        occurred_after: "2026-06-01T00:00:00+00:00",
        occurred_before: null,
        min_similarity: 0.2,
      },
      latency_ms: 410.2,
      result: {
        chunks: [
          {
            content:
              "Mexico 2–1 Canada. Group A, Matchday 1. Goals: Lozano 23', David 41' (CAN), Giménez 78'. " +
              "Played at Estadio Azteca, Mexico City. Attendance 87,523.",
            score: 0.873,
            document_id: "f1c2e3a4-1111-2222-3333-444455556666",
            occurred_at: "2026-06-11T19:00:00+00:00",
          },
          {
            content:
              "Group A standings after Matchday 1: Mexico 3 pts, USA 1 pt, Canada 0 pts, " +
              "New Zealand 1 pt. Mexico lead on goal difference.",
            score: 0.642,
            document_id: "a7b8c9d0-2222-3333-4444-555566667777",
            occurred_at: "2026-06-11T22:00:00+00:00",
          },
          {
            content:
              "Santiago Giménez's late strike sealed a hard-fought opening win for the hosts in front of a sold-out crowd.",
            score: 0.531,
            document_id: "c3d4e5f6-3333-4444-5555-666677778888",
            occurred_at: "2026-06-12T06:30:00+00:00",
          },
        ],
        entities: [
          { name: "Mexico", entity_type: "TEAM", description: "National team, Group A host", score: 0.94 },
          { name: "Canada", entity_type: "TEAM", description: "National team, Group A", score: 0.91 },
          { name: "Hirving Lozano", entity_type: "PLAYER", description: "Mexico winger", score: 0.82 },
          { name: "Santiago Giménez", entity_type: "PLAYER", description: "Mexico forward", score: 0.8 },
          { name: "Jonathan David", entity_type: "PLAYER", description: "Canada forward", score: 0.77 },
          { name: "Estadio Azteca", entity_type: "VENUE", description: "Mexico City stadium", score: 0.69 },
        ],
        relationships: [
          {
            relationship_type: "DEFEATED",
            source_entity_id: "f1c2e3a4-1111-2222-3333-444455556666",
            target_entity_id: "a7b8c9d0-2222-3333-4444-555566667777",
            source_name: "Mexico",
            target_name: "Canada",
            description: "Mexico defeated Canada 2–1 in the Group A opener",
            score: 0.88,
          },
          {
            relationship_type: "PLAYED_AT",
            source_entity_id: "f1c2e3a4-1111-2222-3333-444455556666",
            target_entity_id: "9a9b9c9d-5555-6666-7777-888899990000",
            source_name: "Mexico",
            target_name: "Estadio Azteca",
            description: "The Group A opener was played at Estadio Azteca",
            score: 0.83,
          },
          {
            // source_name unresolved server-side → exercises id/short-id fallback.
            relationship_type: "SCORED_FOR",
            source_entity_id: "11110000-aaaa-bbbb-cccc-ddddeeeeffff",
            target_entity_id: "f1c2e3a4-1111-2222-3333-444455556666",
            source_name: null,
            target_name: "Mexico",
            description: "Hirving Lozano scored for Mexico (23')",
            score: 0.81,
          },
        ],
        documents: [
          {
            title: "Mexico 2–1 Canada — Match Report",
            source_type: "match",
            source_name: "football-data.org",
            source_url: "https://www.football-data.org/match/537327",
            source_timestamp: "2026-06-11T19:00:00",
            external_id: "match:fd:537327",
          },
          {
            title: "Group A — Standings & Results",
            source_type: "standings",
            source_name: "football-data.org",
            source_url: "https://www.football-data.org/competition/WC/standings",
            source_timestamp: "2026-06-11T22:00:00",
            external_id: "standings:fd:WC:A",
          },
        ],
        engine_info: { max_raw_vector_score: 0.441 },
      },
    },
    {
      params: {
        query: "Giménez Lozano goals Mexico opener",
        mode: "semantic",
        limit: 5,
        source_type: null,
        occurred_after: null,
        occurred_before: null,
        min_similarity: 0.0,
      },
      latency_ms: 202.1,
      result: {
        chunks: [
          {
            content:
              "News: Hosts Mexico open World Cup 2026 with a statement win as the Azteca roars. Lozano and Giménez on target.",
            score: 0.588,
            document_id: "d4e5f6a7-4444-5555-6666-777788889999",
            occurred_at: "2026-06-12T07:00:00+00:00",
          },
        ],
        entities: [
          { name: "World Cup 2026", entity_type: "TOURNAMENT", description: "FIFA World Cup, USA/CAN/MEX", score: 0.71 },
        ],
        relationships: [],
        documents: [
          {
            title: "Hosts Mexico open World Cup 2026 with a statement win",
            source_type: "news",
            source_name: "Reuters",
            source_url: "https://www.reuters.com/sports/soccer/mexico-canada-wc2026",
            source_timestamp: "2026-06-12T07:00:00",
            external_id: "news:reuters:wc2026-mex-can",
          },
        ],
        engine_info: { max_raw_vector_score: 0.39 },
      },
    },
  ],
};

export const MOCK_ABSTAINED: AskResponse = {
  question: "Who will win the 2030 World Cup?",
  answer:
    "I don't have grounded information about that in my sources. The store covers the 2026 tournament only.",
  abstained: true,
  metrics: {
    total_latency_ms: 940.0,
    recall_latency_ms: 305.0,
    recall_calls: 1,
    chunks_returned: 0,
    top_score: null,
    max_raw_vector_score: null,
    answer_tokens: { input: 1203, output: 28, total: 1231 },
  },
  recall_trace: [
    {
      params: {
        query: "2030 World Cup winner prediction",
        mode: "hybrid",
        limit: 10,
        source_type: null,
        occurred_after: null,
        occurred_before: null,
        min_similarity: 0.2,
      },
      latency_ms: 305.0,
      result: {
        chunks: [],
        entities: [],
        relationships: [],
        documents: [],
        engine_info: { max_raw_vector_score: null },
      },
    },
  ],
};
