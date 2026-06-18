import { API_BASE, authFetch, iterSSEFrames } from "./client";
import { fetchSSE, withSSETimeout, type SSECallbacks } from "./sse";
import type {
  Question,
  InterviewStartResponse,
  EndInterviewResponse,
  Profile,
  DueReview,
  TopicInfo,
  AlgorithmCard,
  Favorite,
} from "../types/api";

// ── Speech-to-text ──

export async function transcribeAudio(audioBlob: Blob): Promise<any> {
  const form = new FormData();
  form.append("file", audioBlob, "recording.webm");
  const res = await authFetch(`${API_BASE}/transcribe`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getTopics(): Promise<any> {
  const res = await authFetch(`${API_BASE}/topics`);
  return res.json();
}

export async function createTopic(name: string, icon: string = "📝"): Promise<any> {
  const res = await authFetch(`${API_BASE}/topics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, icon }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteTopic(key: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/topics/${encodeURIComponent(key)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── Resume ──

export async function getResumeStatus(): Promise<any> {
  const res = await authFetch(`${API_BASE}/resume/status`);
  return res.json();
}

export async function uploadResume(file: File): Promise<any> {
  const form = new FormData();
  form.append("file", file);
  const res = await authFetch(`${API_BASE}/resume/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function startInterview(mode: string, topic: string | null = null, callbacks?: SSECallbacks): Promise<InterviewStartResponse> {
  return fetchSSE<InterviewStartResponse>(`${API_BASE}/interview/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, topic }),
  }, callbacks);
}

interface StreamCallbacks {
  onQuestion?: (data: Question) => void;
  onQuestionUpdate?: (data: Question) => void;
  onDone?: (event: any) => void;
  onError?: (message: string) => void;
  onStage?: (event: PipelineStageEvent) => void;
}

export interface RAGRetrievalMetrics {
  relevance: number;
  coverage: number;
  diversity: number;
  chunk_details: { score: number; source: string }[];
}

export interface PipelineStageEvent {
  stage: string;
  label?: string;
  status: "start" | "ok" | "error";
  ts: number;
  duration_ms?: number;
  detail?: string;
  rag_metrics?: RAGRetrievalMetrics;
}

export async function startInterviewStream(mode: string, topic: string | null, { onQuestion, onQuestionUpdate, onDone, onError, onStage }: StreamCallbacks, externalSignal?: AbortSignal): Promise<void> {
  await withSSETimeout(async (signal) => {
    const res = await authFetch(`${API_BASE}/interview/start-stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, topic }),
      signal,
    });
    if (!res.ok) throw new Error(await res.text());

    for await (const event of iterSSEFrames(res)) {
      if (event.type === "question" && onQuestion) onQuestion(event.data);
      else if (event.type === "question_update" && onQuestionUpdate) onQuestionUpdate(event.data);
      else if (event.type === "done" && onDone) onDone(event);
      else if (event.type === "error" && onError) onError(event.message);
      else if (event.type === "pipeline_stage" && onStage) onStage(event as PipelineStageEvent);
    }
  }, undefined, externalSignal);
}

export async function previewJobPrep(payload: any, callbacks?: SSECallbacks): Promise<any> {
  return fetchSSE(`${API_BASE}/job-prep/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }, callbacks);
}

export async function startJobPrep(payload: any, callbacks?: SSECallbacks): Promise<any> {
  return fetchSSE(`${API_BASE}/job-prep/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }, callbacks);
}

export async function sendMessage(sessionId: string, message: string, callbacks?: SSECallbacks): Promise<any> {
  return fetchSSE(`${API_BASE}/interview/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
  }, callbacks);
}

export interface RAGEvalMetrics {
  faithfulness: number;
  answer_relevance: number;
  answer_correctness: number;
  per_question: { question_id: number; faithfulness: number; answer_relevance: number }[];
}

interface EndInterviewCallbacks {
  onProgress?: (message: string) => void;
  onRAGMetrics?: (data: RAGEvalMetrics) => void;
}

export async function endInterview(
  sessionId: string,
  answers: any[] | null = null,
  callbacks?: EndInterviewCallbacks,
): Promise<EndInterviewResponse> {
  const options: RequestInit = { method: "POST" };
  if (answers) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify({ answers });
  }
  return withSSETimeout<EndInterviewResponse>(async (signal) => {
    const res = await authFetch(`${API_BASE}/interview/end/${sessionId}`, {
      ...options,
      signal,
    });
    if (!res.ok) throw new Error(await res.text());

    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("text/event-stream")) {
      return res.json();
    }

    let result: EndInterviewResponse | null = null;

    for await (const event of iterSSEFrames(res)) {
      if (event.type === "eval_progress" && callbacks?.onProgress) {
        callbacks.onProgress(event.message);
      } else if (event.type === "rag_eval_metrics" && callbacks?.onRAGMetrics) {
        callbacks.onRAGMetrics(event.data);
      } else if (event.type === "complete") {
        result = event.data;
      }
    }

    if (!result) throw new Error("评估流结束但未收到结果");
    return result;
  });
}

export async function getReview(sessionId: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/interview/review/${sessionId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/**
 * Manually apply profile / SR / knowledge side-effects for an already-scored
 * session whose stats never landed. Idempotent on the server (meta.synced_at):
 * status is "synced" on first apply, "already_synced" if it was applied before.
 */
export async function syncSession(
  sessionId: string,
): Promise<{ status: "synced" | "already_synced"; synced_at?: string }> {
  const res = await authFetch(`${API_BASE}/interview/sync/${sessionId}`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getReferenceAnswer(
  topic: string,
  question: string,
  sessionId: string | null = null,
  questionId: number | string | null = null,
  force: boolean = false,
  mode: string = "full",
  callbacks?: SSECallbacks,
): Promise<any> {
  return fetchSSE(`${API_BASE}/interview/reference-answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic, question, session_id: sessionId, question_id: questionId, force, mode }),
  }, callbacks);
}

export async function getHistory(
  limit: number = 20,
  offset: number = 0,
  mode: string | null = null,
  topic: string | null = null,
  status: "completed" | "in_progress" | "all" = "completed",
): Promise<any> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset), status });
  if (mode) params.set("mode", mode);
  if (topic) params.set("topic", topic);
  const res = await authFetch(`${API_BASE}/interview/history?${params}`);
  return res.json();
}

export async function getInterviewSession(sessionId: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/interview/session/${sessionId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface DrillProgressPayload {
  current_index: number;
  partial_answers: Record<string | number, string>;
  hints: Record<string | number, any>;
}

export async function saveDrillProgress(
  sessionId: string,
  payload: DrillProgressPayload,
  opts: { keepalive?: boolean } = {},
): Promise<void> {
  const res = await authFetch(`${API_BASE}/interview/session/${sessionId}/progress`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    // keepalive lets the request survive page unload (pagehide / tab close),
    // so the final flush isn't dropped when the user exits mid-drill.
    keepalive: opts.keepalive,
  });
  // Surface backend failures (404/4xx) instead of silently resolving — the
  // caller's catch keeps the localStorage fallback + shows the retry toast.
  if (!res.ok) throw new Error(await res.text());
}

export async function deleteSession(sessionId: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/interview/session/${sessionId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getInterviewTopics(): Promise<any> {
  const res = await authFetch(`${API_BASE}/interview/topics`);
  return res.json();
}

// ── Graph ──

export async function getGraphData(topic: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/graph/${topic}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── Profile & Retrospective ──

export async function getProfile(): Promise<Profile> {
  const res = await authFetch(`${API_BASE}/profile`);
  return res.json();
}

export async function getDueReviews(topic?: string): Promise<DueReview[]> {
  const url = topic
    ? `${API_BASE}/profile/due-reviews?topic=${encodeURIComponent(topic)}`
    : `${API_BASE}/profile/due-reviews`;
  const res = await authFetch(url);
  return res.json();
}

export async function exportProfile(): Promise<any> {
  const res = await authFetch(`${API_BASE}/profile/export`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getTopicRetrospective(topic: string, callbacks?: SSECallbacks): Promise<any> {
  return fetchSSE(`${API_BASE}/profile/topic/${topic}/retrospective`, {
    method: "POST",
  }, callbacks);
}

export async function getTopicHistory(topic: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/profile/topic/${topic}/history`);
  return res.json();
}

// ── Knowledge management ──

export async function getCoreKnowledge(topic: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/knowledge/${encodeURIComponent(topic)}/core`);
  return res.json();
}

export async function updateCoreKnowledge(topic: string, filename: string, content: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/knowledge/${encodeURIComponent(topic)}/core/${encodeURIComponent(filename)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteCoreKnowledge(topic: string, filename: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/knowledge/${encodeURIComponent(topic)}/core/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function createCoreKnowledge(topic: string, filename: string, content: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/knowledge/${encodeURIComponent(topic)}/core`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, content }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function uploadCoreKnowledgeFiles(topic: string, files: File[]): Promise<any> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  const res = await authFetch(`${API_BASE}/knowledge/${encodeURIComponent(topic)}/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function generateKnowledge(topic: string, callbacks?: SSECallbacks): Promise<any> {
  return fetchSSE(`${API_BASE}/knowledge/${encodeURIComponent(topic)}/generate`, {
    method: "POST",
  }, callbacks);
}

export interface RebuildTaskSubmission {
  ok: boolean;
  task_id: string;
  topic: string;
  file_count: number;
  message: string;
}

export interface RebuildAllSubmission {
  ok: boolean;
  total: number;
  tasks: { task_id: string; topic: string; file_count: number }[];
  message: string;
}

export interface RebuildTaskStatus {
  task_id: string;
  user_id: string;
  topic: string;
  label: string;
  state: "pending" | "running" | "completed" | "failed";
  submitted_at: number;
  started_at: number;
  finished_at: number;
  file_count: number;
  retry_count: number;
  error: string;
  message: string;
  progress_done: number;
  progress_total: number;
}

/** Submit a single-topic rebuild. Returns immediately with task_id; poll status separately. */
export async function rebuildTopicIndex(topic: string): Promise<RebuildTaskSubmission> {
  const res = await authFetch(`${API_BASE}/knowledge/${encodeURIComponent(topic)}/rebuild`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/** Submit rebuilds for all topics. Returns immediately with the list of task_ids. */
export async function rebuildAllIndices(): Promise<RebuildAllSubmission> {
  const res = await authFetch(`${API_BASE}/knowledge/rebuild-all`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/** Fetch all rebuild task statuses for the current user (newest first). */
export async function getRebuildStatus(): Promise<{ tasks: RebuildTaskStatus[] }> {
  const res = await authFetch(`${API_BASE}/knowledge/rebuild-status`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── Recording review ──

export async function transcribeRecording(audioBlob: Blob, mode: string = "dual"): Promise<any> {
  const form = new FormData();
  form.append("file", audioBlob, (audioBlob as any).name || "recording.webm");
  form.append("mode", mode);
  const res = await authFetch(`${API_BASE}/recording/transcribe`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function analyzeRecording(
  transcript: string,
  recordingMode: string,
  company?: string,
  position?: string,
  callbacks?: SSECallbacks,
): Promise<any> {
  const body: any = { transcript, recording_mode: recordingMode };
  if (company) body.company = company;
  if (position) body.position = position;
  return fetchSSE(`${API_BASE}/recording/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, callbacks);
}

export async function getHighFreq(topic: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/knowledge/${encodeURIComponent(topic)}/high_freq`);
  return res.json();
}

export async function updateHighFreq(topic: string, content: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/knowledge/${encodeURIComponent(topic)}/high_freq`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface KnowledgeStats {
  topic: string;
  file_count: number;
  chunk_count: number;
  last_any_update_at: number;
  last_evolved_at: number;
  last_evolved_file: string;
  evolution_count: number;
  last_high_freq_at: number;
  high_freq_size: number;
}

export async function getKnowledgeStats(topic: string): Promise<KnowledgeStats> {
  const res = await authFetch(`${API_BASE}/knowledge/${encodeURIComponent(topic)}/stats`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface ChunkCounts {
  counts: Record<string, number>;
  total: number;
}

export async function getChunkCounts(): Promise<ChunkCounts> {
  const res = await authFetch(`${API_BASE}/knowledge/chunk-counts`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── Favorites ──

export async function addFavorite(data: any): Promise<any> {
  const res = await authFetch(`${API_BASE}/favorites`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

interface FavoritesParams {
  topic?: string;
  tag?: string;
  sort_by?: string;
  sort_order?: string;
  limit?: number | string;
  offset?: number | string;
}

export async function getFavorites(params: FavoritesParams = {}): Promise<any> {
  const qs = new URLSearchParams();
  if (params.topic) qs.set("topic", params.topic);
  if (params.tag) qs.set("tag", params.tag);
  if (params.sort_by) qs.set("sort_by", params.sort_by);
  if (params.sort_order) qs.set("sort_order", params.sort_order);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  const res = await authFetch(`${API_BASE}/favorites?${qs}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateFavorite(id: string, data: any): Promise<any> {
  const res = await authFetch(`${API_BASE}/favorites/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteFavorite(id: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/favorites/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getFavoriteTags(): Promise<any> {
  const res = await authFetch(`${API_BASE}/favorites/tags`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function exportFavorites(format: string, ids: string[] | null = null, topic: string | null = null): Promise<Response> {
  const res = await authFetch(`${API_BASE}/favorites/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format, ids, topic }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res;
}

// ── Algorithm Solver ──

export async function solveAlgorithm(problemText: string, language: string = "python", sourceUrl: string = "", callbacks?: SSECallbacks): Promise<any> {
  return fetchSSE(`${API_BASE}/algorithm/solve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ problem_text: problemText, language, source_url: sourceUrl }),
  }, callbacks);
}

export async function chatAlgorithm(sessionId: string, message: string, callbacks?: SSECallbacks): Promise<any> {
  return fetchSSE(`${API_BASE}/algorithm/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
  }, callbacks);
}

export async function saveAlgorithmCard(sessionId: string, title: string, difficulty: string = "", tags: string[] = [], note: string = ""): Promise<any> {
  const res = await authFetch(`${API_BASE}/algorithm/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, title, difficulty, tags, note }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

interface AlgorithmCardsParams {
  difficulty?: string;
  tag?: string;
  search?: string;
  sort_by?: string;
  sort_order?: string;
  limit?: number | string;
  offset?: number | string;
}

export async function getAlgorithmCards(params: AlgorithmCardsParams = {}): Promise<any> {
  const qs = new URLSearchParams();
  if (params.difficulty) qs.set("difficulty", params.difficulty);
  if (params.tag) qs.set("tag", params.tag);
  if (params.search) qs.set("search", params.search);
  if (params.sort_by) qs.set("sort_by", params.sort_by);
  if (params.sort_order) qs.set("sort_order", params.sort_order);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  const res = await authFetch(`${API_BASE}/algorithm/cards?${qs}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getAlgorithmCard(id: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/algorithm/cards/${id}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateAlgorithmCard(id: string, data: any): Promise<any> {
  const res = await authFetch(`${API_BASE}/algorithm/cards/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteAlgorithmCard(id: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/algorithm/cards/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getAlgorithmTags(): Promise<any> {
  const res = await authFetch(`${API_BASE}/algorithm/tags`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function exportAlgorithmCards(format: string, ids: string[] | null = null, difficulty: string | null = null): Promise<Response> {
  const res = await authFetch(`${API_BASE}/algorithm/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format, ids, difficulty }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res;
}

// ── RAG Metrics ──

export interface RAGMetricsRecord {
  id: number;
  session_id: string;
  topic: string;
  stage: string;
  // question_gen stage (retrieval health gauges)
  relevance: number | null;
  coverage: number | null;
  diversity: number | null;
  // legacy columns kept for historical rows (older question_gen records):
  // `discrimination` was superseded by `coverage`; context_* predate both.
  discrimination: number | null;
  context_relevance: number | null;
  context_precision: number | null;
  context_recall: number | null;
  // answer_eval stage (generation-side, LLM-judged)
  faithfulness: number | null;
  answer_relevance: number | null;
  answer_correctness: number | null;
  chunk_count: number | null;
  detail: Record<string, any>;
  created_at: string;
}

export async function getRAGMetrics(params: {
  topic?: string;
  stage?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<RAGMetricsRecord[]> {
  const qs = new URLSearchParams();
  if (params.topic) qs.set("topic", params.topic);
  if (params.stage) qs.set("stage", params.stage);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  const res = await authFetch(`${API_BASE}/interview/rag-metrics?${qs}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getSessionRAGMetrics(sessionId: string): Promise<RAGMetricsRecord[]> {
  const res = await authFetch(`${API_BASE}/interview/rag-metrics/${sessionId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
