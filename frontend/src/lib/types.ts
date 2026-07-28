/** API contract types.
 *
 * Hand-written here for build simplicity; TDD §5 specifies generating these
 * from the live OpenAPI schema (`openapi-typescript`) so a backend field rename
 * becomes a frontend compile error. The generation step is `make types`.
 */

export type Role = "VIEWER" | "MEMBER" | "ADMIN" | "OWNER";

export type DocumentStatus =
  | "PENDING"
  | "PROCESSING"
  | "READY"
  | "FAILED"
  | "QUARANTINED"
  | "DELETED";

export interface UserProfile {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface Membership {
  org_id: string;
  org_name: string;
  org_slug: string;
  role: Role;
}

export interface Me {
  user: UserProfile;
  memberships: Membership[];
}

export interface Workspace {
  id: string;
  org_id: string;
  name: string;
  slug: string;
  description: string | null;
  created_at: string;
  role: Role | null;
  document_count: number;
  ready_document_count: number;
  chunk_count: number;
}

export interface Doc {
  id: string;
  workspace_id: string;
  title: string;
  source_type: string;
  source_uri: string | null;
  mime_type: string | null;
  byte_size: number | null;
  status: DocumentStatus;
  error_message: string | null;
  page_count: number | null;
  chunk_count: number;
  token_count: number;
  created_at: string;
  processed_at: string | null;
  uploaded_by: string;
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  document_title: string;
  content: string;
  page_label: string | null;
  section: string | null;
  score: number;
  dense_rank: number | null;
  sparse_rank: number | null;
  found_by_both: boolean;
}

export interface SearchResponse {
  query: string;
  hits: SearchHit[];
  dense_candidates: number;
  sparse_candidates: number;
  fused_candidates: number;
  took_ms: number;
}

export interface Conversation {
  id: string;
  workspace_id: string;
  user_id: string;
  title: string;
  created_at: string;
  last_message_at: string | null;
  message_count: number;
}

export interface CitationRef {
  marker: number;
  chunk_id: string | null;
  document_id: string | null;
  document_title: string | null;
  page_label: string | null;
  score: number | null;
  snippet: string | null;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: "USER" | "ASSISTANT" | "SYSTEM";
  content: string;
  model: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  ttft_ms: number | null;
  latency_ms: number | null;
  finish_reason: string | null;
  groundedness: number | null;
  created_at: string;
  citations: CitationRef[];
}

/** A source as delivered by the SSE `meta` frame — before the first token. */
export interface StreamSource {
  marker: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  page_label: string | null;
  section: string | null;
  score: number;
  found_by_both: boolean;
  snippet: string;
}

export interface StreamMeta {
  message_id: string;
  provider: string;
  model: string;
  retrieval_ms: number;
  context_tokens?: number;
  refused: boolean;
  /** Raw cosine similarity of the best candidate — the value the refusal gate
   *  actually compares. Present on refusals too, so the meter can show the
   *  needle below the line. */
  relevance: number;
  floor: number;
  sources: StreamSource[];
}

export interface StreamUsage {
  input_tokens: number;
  output_tokens: number;
  ttft_ms: number | null;
  latency_ms: number;
  model: string;
}

export interface StreamCitations {
  validated: number[];
  stripped: number[];
  groundedness: number | null;
}

export interface Overview {
  documents: number;
  documents_ready: number;
  documents_failed: number;
  chunks: number;
  conversations: number;
  messages: number;
  answered: number;
  refused: number;
  refusal_rate: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  ttft_p50_ms: number | null;
  avg_groundedness: number | null;
  total_cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  cost_per_answer_usd: number;
}

export interface QualityMetrics {
  feedback_count: number;
  positive_feedback: number;
  satisfaction_rate: number | null;
  groundedness_p10: number | null;
  groundedness_p50: number | null;
  total_citations: number;
  citations_per_answer: number;
}

export interface UsagePoint {
  date: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  calls: number;
}

export interface DocLeaderboardEntry {
  document_id: string;
  title: string;
  chunks: number;
  citations: number;
}

export interface SystemStatus {
  environment: string;
  llm_provider: string;
  chat_model: string;
  llm_configured: boolean;
  embedding_provider: string;
  embedding_model: string;
  embedding_dimensions: number;
  vector_count: number;
  queue_pending: number;
  queue_processing: number;
  relevance_floor: number;
  retrieval_top_k: number;
  chunk_size_chars: number;
  chunk_overlap_chars: number;
}
