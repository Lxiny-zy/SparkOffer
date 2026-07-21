import { API_BASE, authFetch } from "./client";

// RAG eval client. The backend exposes two intentionally different suites:
// frozen retrieval regression and synthetic end-to-end judging. Keep their
// dimensions explicit so the dashboard never treats unlike runs as one trend.

export type RagEvalKind = "frozen_retrieval" | "synthetic_e2e";
export type RagRetrievalMode = "atomic_dense" | "production_replay";

export interface RagEvalManifest {
  schema_version?: number;
  metric_semantics_version?: number;
  created_at?: string;
  eval_kind?: RagEvalKind | string;
  retrieval_mode?: RagRetrievalMode | string;
  topic?: string;
  user_id?: string;
  dataset?: {
    id?: string;
    version?: string;
    hash?: string;
    case_ids?: string[];
  };
  corpus?: { hash?: string; file_count?: number };
  k?: number;
  judge_mode?: string;
  seed?: number;
  git_sha?: string;
  index_revision?: string;
  comparison_signature?: string;
  comparison_dimensions?: Record<string, unknown>;
  state_stable?: boolean;
  post_run_comparison_signature?: string;
  runtime?: Record<string, unknown>;
  indexing?: Record<string, unknown>;
  retrieval_config?: Record<string, unknown>;
  protocol?: Record<string, unknown>;
  observations?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface RagEvalSummary {
  hit_at_k: number | null;
  hit_at_k_strict: number | null;
  mrr: number | null;
  ndcg_at_k: number | null;
  context_precision: number | null;
  context_recall: number | null;
  faithfulness: number | null;
  answer_relevancy: number | null;
  answer_correctness: number | null;
  n_questions: number;
  evaluated_questions?: number | null;
  error_count: number | null;
  success_rate: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  valid?: boolean;
  degraded_count?: number;
  fully_healthy_rate?: number;
  comparable?: boolean;
  generation_success_rate?: number | null;
  judge_observed_rate?: number | null;
  metric_observation_rate?: number | null;
}

export interface RagEvalQuestionDetail {
  id?: string;
  question: string;
  reference_answer?: string;
  generated_answer?: string;
  rank?: number | null;
  hit?: number;
  trivial_hit?: boolean;
  loo_hit?: number;        // 留一法泛化命中（剔源 chunk 后答案是否仍被覆盖）
  context_precision: number | null;
  context_recall: number | null;
  faithfulness?: number | null;
  answer_relevancy?: number | null;
  answer_correctness?: number | null;
  match_method?: string;
  gold_source?: string;
  difficulty?: string;
  type?: string;
  bundle_id?: string;
  outcome?: string;
  retrieval_status?: string;
  retrieval_error?: string;
  retrieval_latency_ms?: number;
  generation_success?: boolean;
  judge_successes?: number;
  judge_attempts?: number;
  judge_observed_rate?: number | null;
  metric_observation_success?: boolean;
  error_code?: string;
  error?: string;
  hit_at_k?: number | null;
  ndcg_at_k?: number | null;
  mrr?: number | null;
  n_chunks?: number;
  n_relevant_chunks?: number;
  latency_ms?: number;
}

export interface RagEvalDetail {
  eval_kind?: RagEvalKind | string;
  retrieval_mode?: RagRetrievalMode | string;
  seed?: number;
  dataset_id?: string;
  dataset_version?: string;
  dataset_hash?: string;
  corpus_hash?: string;
  manifest?: RagEvalManifest | null;
  judge_mode?: string;
  k?: number;
  error_count?: number;
  groups?: Record<string, unknown>;
  bundles?: Array<Record<string, unknown>>;
  questions?: RagEvalQuestionDetail[];
}

export type RagEvalState = "pending" | "running" | "completed" | "failed";

export interface RagEvalStatus {
  job_id: string;
  topic: string;
  status: RagEvalState;
  phase: string;
  done: number;
  total: number;
  n_questions: number | null;
  judge_mode: string;
  eval_kind?: RagEvalKind | string;
  retrieval_mode?: RagRetrievalMode | string;
  seed?: number;
  error: string | null;
  summary: RagEvalSummary | null;
  detail: RagEvalDetail | null;
  manifest?: RagEvalManifest | null;
  run_id: number | null;
  started_at: number | null;
  updated_at: number | null;
}

export interface RagEvalRun {
  id: number;
  job_id: string;
  topic: string;
  scope: string;
  n_questions: number;
  k: number;
  judge_mode: string;
  eval_kind?: RagEvalKind | string;
  retrieval_mode?: RagRetrievalMode | string;
  dataset_id?: string;
  dataset_version?: string;
  dataset_hash?: string;
  corpus_hash?: string;
  seed?: number | null;
  hit_at_k: number | null;
  hit_at_k_strict: number | null;
  mrr: number | null;
  ndcg_at_k: number | null;
  context_precision: number | null;
  context_recall: number | null;
  faithfulness: number | null;
  answer_relevancy: number | null;
  answer_correctness: number | null;
  success_rate: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  status: string;
  error: string;
  manifest?: RagEvalManifest | null;
  detail?: RagEvalDetail;
  created_at: string;
}

export interface StartRagEvalOpts {
  topic: string;
  n_questions?: number;
  k?: number;
  judge_mode?: "standard" | "full";
  eval_kind?: RagEvalKind;
  retrieval_mode?: RagRetrievalMode;
  seed?: number;
}

export interface StartRagEvalResponse {
  job_id: string;
  topic: string;
  n_questions: number;
  judge_mode: string;
  eval_kind?: RagEvalKind | string;
  retrieval_mode?: RagRetrievalMode | string;
  seed?: number;
  reused?: boolean;
}

export interface RagEvalRunFilters {
  eval_kind?: RagEvalKind;
  retrieval_mode?: RagRetrievalMode;
  offset?: number;
}

async function _detailError(res: Response, fallback: string): Promise<never> {
  let msg = `${fallback} (${res.status})`;
  try {
    const j = await res.json();
    if (j?.detail) msg = j.detail;
  } catch {
    // non-JSON error body — keep fallback
  }
  throw new Error(msg);
}

export async function startRagEval(
  opts: StartRagEvalOpts,
): Promise<StartRagEvalResponse> {
  const res = await authFetch(`${API_BASE}/rag-eval/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts),
  });
  if (!res.ok) return _detailError(res, "启动评测失败");
  return res.json();
}

export async function getRagEvalStatus(jobId: string): Promise<RagEvalStatus> {
  const res = await authFetch(`${API_BASE}/rag-eval/status/${jobId}`);
  if (!res.ok) return _detailError(res, "查询进度失败");
  return res.json();
}

export async function listRagEvalRuns(
  topic?: string,
  limit = 20,
  filters: RagEvalRunFilters = {},
): Promise<RagEvalRun[]> {
  const qs = new URLSearchParams();
  if (topic) qs.set("topic", topic);
  if (filters.eval_kind) qs.set("eval_kind", filters.eval_kind);
  if (filters.retrieval_mode) qs.set("retrieval_mode", filters.retrieval_mode);
  qs.set("limit", String(limit));
  if (filters.offset != null) qs.set("offset", String(filters.offset));
  const res = await authFetch(`${API_BASE}/rag-eval/runs?${qs.toString()}`);
  if (!res.ok) return _detailError(res, "加载评测历史失败");
  return res.json();
}

export async function getRagEvalRun(runId: number): Promise<RagEvalRun> {
  const res = await authFetch(`${API_BASE}/rag-eval/runs/${runId}`);
  if (!res.ok) return _detailError(res, "加载评测详情失败");
  return res.json();
}
