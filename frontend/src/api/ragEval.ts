import { API_BASE, authFetch } from "./client";

// RAG eval (true RAGAS benchmark) client. Backend runs an async job; we poll
// /status until it's completed|failed. Mirrors the authFetch + API_BASE house
// style in client.ts.

export interface RagEvalSummary {
  hit_at_k: number | null;
  mrr: number | null;
  context_precision: number | null;
  context_recall: number | null;
  faithfulness: number | null;
  answer_relevancy: number | null;
  answer_correctness: number | null;
  n_questions: number;
  error_count: number;
}

export interface RagEvalQuestionDetail {
  question: string;
  reference_answer: string;
  generated_answer: string;
  rank: number | null;
  hit: number;
  context_precision: number | null;
  context_recall: number | null;
  faithfulness: number | null;
  answer_relevancy: number | null;
  answer_correctness: number | null;
  match_method: string;
  gold_source: string;
}

export interface RagEvalDetail {
  judge_mode?: string;
  k?: number;
  error_count?: number;
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
  error: string | null;
  summary: RagEvalSummary | null;
  detail: RagEvalDetail | null;
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
  hit_at_k: number | null;
  mrr: number | null;
  context_precision: number | null;
  context_recall: number | null;
  faithfulness: number | null;
  answer_relevancy: number | null;
  answer_correctness: number | null;
  status: string;
  error: string;
  detail: RagEvalDetail;
  created_at: string;
}

export interface StartRagEvalOpts {
  topic: string;
  n_questions?: number;
  k?: number;
  judge_mode?: "standard" | "full";
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
): Promise<{ job_id: string; topic: string; n_questions: number; judge_mode: string }> {
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

export async function listRagEvalRuns(topic?: string, limit = 20): Promise<RagEvalRun[]> {
  const qs = new URLSearchParams();
  if (topic) qs.set("topic", topic);
  qs.set("limit", String(limit));
  const res = await authFetch(`${API_BASE}/rag-eval/runs?${qs.toString()}`);
  if (!res.ok) return _detailError(res, "加载评测历史失败");
  return res.json();
}
