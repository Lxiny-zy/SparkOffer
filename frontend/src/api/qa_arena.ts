import { API_BASE, authHeaders, handleStreamUnauthorized, iterSSEFrames } from "./client";

export interface QASession {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface QAMessage {
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface QASummaryResult {
  content: string;
  filename: string;
  topic: string;
}

// ── Session CRUD ──

export async function createQASession(title?: string): Promise<QASession> {
  const res = await fetch(`${API_BASE}/qa-arena/sessions`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(title ? { title } : {}),
  });
  if (!res.ok) throw new Error("创建会话失败");
  return res.json();
}

export async function listQASessions(limit = 50, offset = 0): Promise<{ sessions: QASession[]; total: number }> {
  const res = await fetch(`${API_BASE}/qa-arena/sessions?limit=${limit}&offset=${offset}`, {
    headers: authHeaders(),
  });
  if (!res.ok) return { sessions: [], total: 0 };
  return res.json();
}

export async function deleteQASession(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/qa-arena/sessions/${sessionId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error("删除会话失败");
}

export async function renameQASession(sessionId: string, title: string): Promise<void> {
  const res = await fetch(`${API_BASE}/qa-arena/sessions/${sessionId}`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error("重命名失败");
}

// ── Messages ──

export async function loadQAMessages(sessionId: string): Promise<QAMessage[]> {
  const res = await fetch(`${API_BASE}/qa-arena/sessions/${sessionId}/messages`, {
    headers: authHeaders(),
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.messages || [];
}

export async function clearQAMessages(sessionId: string): Promise<void> {
  await fetch(`${API_BASE}/qa-arena/sessions/${sessionId}/messages`, {
    method: "DELETE",
    headers: authHeaders(),
  });
}

// ── Streaming Chat ──

export async function* streamQAChat(sessionId: string, message: string, signal?: AbortSignal): AsyncGenerator<any> {
  const res = await fetch(`${API_BASE}/qa-arena/sessions/${sessionId}/chat`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message }),
    signal,
  });

  if (handleStreamUnauthorized(res)) {
    return;
  }

  if (!res.ok) throw new Error(`问答演练场错误: ${res.status}`);

  for await (const event of iterSSEFrames(res)) {
    yield event;
  }
}

// ── Summary ──

export async function generateQASummary(
  sessionId: string,
  onProgress?: (msg: string) => void,
  effort?: string,
): Promise<QASummaryResult> {
  const qs = effort ? `?effort=${encodeURIComponent(effort)}` : "";
  const res = await fetch(`${API_BASE}/qa-arena/sessions/${sessionId}/summary${qs}`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "生成总结失败");
  }

  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("text/event-stream")) {
    return res.json();
  }

  let result: QASummaryResult | null = null;

  for await (const event of iterSSEFrames(res)) {
    if (event.type === "progress" && onProgress) {
      onProgress(event.message);
    } else if (event.type === "error") {
      throw new Error(event.message);
    } else if (event.type === "complete") {
      result = event.data;
    }
  }

  if (!result) throw new Error("生成失败：未收到结果");
  return result;
}

export function downloadMarkdown(content: string, filename: string) {
  const blob = new Blob([content], { type: "text/markdown; charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
