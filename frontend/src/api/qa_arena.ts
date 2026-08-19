import { API_BASE, authFetch, authHeaders, iterSSEFrames } from "./client";
import { SSETerminalError, withSSETimeout, withSSETimeoutGen } from "./sse";

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
  // Transient thinking trace streamed by reasoning models (reasoning_content). Display-only:
  // populated live during a turn, never persisted, so it's absent on reload.
  reasoning?: string;
  // Attached image URLs ready to render: authenticated server URLs for loaded
  // history (fetched via fetchAuthedImage), or local object/data URLs for a
  // message the user just sent this session.
  images?: string[];
}

export interface QASummaryResult {
  content: string;
  filename: string;
  topic: string;
}

// ── Session CRUD ──

export async function createQASession(title?: string): Promise<QASession> {
  const res = await authFetch(`${API_BASE}/qa-arena/sessions`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(title ? { title } : {}),
  });
  if (!res.ok) throw new Error("创建会话失败");
  return res.json();
}

export async function listQASessions(limit = 50, offset = 0): Promise<{ sessions: QASession[]; total: number }> {
  const res = await authFetch(`${API_BASE}/qa-arena/sessions?limit=${limit}&offset=${offset}`);
  if (!res.ok) throw new Error(`Failed to load sessions (${res.status})`);
  return res.json();
}

export async function deleteQASession(sessionId: string): Promise<void> {
  const res = await authFetch(`${API_BASE}/qa-arena/sessions/${sessionId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error("删除会话失败");
}

export async function renameQASession(sessionId: string, title: string): Promise<void> {
  const res = await authFetch(`${API_BASE}/qa-arena/sessions/${sessionId}`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error("重命名失败");
}

// ── Messages ──

// URL of a stored chat image. Auth-protected on the backend — load it via
// fetchAuthedImage (an <img src> can't send the Authorization header).
export function qaImageUrl(sessionId: string, name: string): string {
  return `${API_BASE}/qa-arena/sessions/${sessionId}/images/${encodeURIComponent(name)}`;
}

// Fetch an auth-protected image and return an object URL usable as <img src>.
// The caller must URL.revokeObjectURL it on unmount. Local blob:/data: URLs are
// returned as-is (already directly renderable).
export async function fetchAuthedImage(url: string, signal?: AbortSignal): Promise<string> {
  if (url.startsWith("blob:") || url.startsWith("data:")) return url;
  const res = await authFetch(url, { signal });
  if (!res.ok) throw new Error(`图片加载失败: ${res.status}`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export async function loadQAMessages(sessionId: string): Promise<QAMessage[]> {
  const res = await authFetch(`${API_BASE}/qa-arena/sessions/${sessionId}/messages`);
  if (!res.ok) throw new Error(`Failed to load messages (${res.status})`);
  const data = await res.json();
  return (data.messages || []).map((m: any) => ({
    ...m,
    // Backend stores bare filenames; turn them into auth'd serve URLs for display.
    images: Array.isArray(m.images) ? m.images.map((n: string) => qaImageUrl(sessionId, n)) : [],
  }));
}

export async function clearQAMessages(sessionId: string): Promise<void> {
  const res = await authFetch(`${API_BASE}/qa-arena/sessions/${sessionId}/messages`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to clear messages (${res.status})`);
}

// ── Streaming Chat ──

export async function* streamQAChat(sessionId: string, message: string, images: string[] | undefined, signal?: AbortSignal): AsyncGenerator<any> {
  yield* withSSETimeoutGen(async function* (innerSignal) {
    const res = await authFetch(`${API_BASE}/qa-arena/sessions/${sessionId}/chat`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ message, images: images || [] }),
      signal: innerSignal,
    });

    if (!res.ok) throw new Error(`问答演练场错误: ${res.status}`);

    let sawDone = false;
    let errorMessage: string | null = null;
    for await (const event of iterSSEFrames(res)) {
      if (event.type === "error") {
        errorMessage = event.message || "问答生成失败，请稍后重试";
        continue;
      }
      if (event.type === "done") {
        sawDone = true;
        if (event.terminal === "error" && !errorMessage) errorMessage = "问答生成失败，请稍后重试";
        continue;
      }
      yield event;
    }
    if (errorMessage) throw new SSETerminalError(errorMessage, "error");
    if (!sawDone) throw new SSETerminalError("问答连接提前关闭，未收到完成事件，请重试");
  }, undefined, signal);
}

// Re-answer the last user question (regenerate). No body: the backend re-uses the
// last stored user message and drops any trailing (broken/partial/empty) AI reply.
export async function* regenerateQAChat(sessionId: string, signal?: AbortSignal): AsyncGenerator<any> {
  yield* withSSETimeoutGen(async function* (innerSignal) {
    const res = await authFetch(`${API_BASE}/qa-arena/sessions/${sessionId}/regenerate`, {
      method: "POST",
      headers: authHeaders(),
      signal: innerSignal,
    });

    if (!res.ok) throw new Error(`重新生成失败: ${res.status}`);

    let sawDone = false;
    let errorMessage: string | null = null;
    for await (const event of iterSSEFrames(res)) {
      if (event.type === "error") {
        errorMessage = event.message || "重新生成失败，请稍后重试";
        continue;
      }
      if (event.type === "done") {
        sawDone = true;
        if (event.terminal === "error" && !errorMessage) errorMessage = "重新生成失败，请稍后重试";
        continue;
      }
      yield event;
    }
    if (errorMessage) throw new SSETerminalError(errorMessage, "error");
    if (!sawDone) throw new SSETerminalError("问答连接提前关闭，未收到完成事件，请重试");
  }, undefined, signal);
}

// ── Summary ──

export async function generateQASummary(
  sessionId: string,
  onProgress?: (msg: string) => void,
  effort?: string,
  signal?: AbortSignal,
): Promise<QASummaryResult> {
  const qs = effort ? `?effort=${encodeURIComponent(effort)}` : "";
  return withSSETimeout(async (innerSignal) => {
    const res = await authFetch(`${API_BASE}/qa-arena/sessions/${sessionId}/summary${qs}`, {
      method: "POST",
      headers: authHeaders(),
      signal: innerSignal,
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
    let sawDone = false;
    let errorMessage: string | null = null;

    for await (const event of iterSSEFrames(res)) {
      if (event.type === "progress" && onProgress) {
        onProgress(event.message);
      } else if (event.type === "error") {
        errorMessage = event.message || "生成总结失败，请稍后重试";
      } else if (event.type === "complete") {
        result = event.data;
      } else if (event.type === "done") {
        sawDone = true;
        if (event.terminal === "error" && !errorMessage) errorMessage = "生成总结失败，请稍后重试";
      }
    }

    if (errorMessage) throw new SSETerminalError(errorMessage, "error");
    if (!sawDone) throw new SSETerminalError("总结连接提前关闭，未收到完成事件，请重试");
    if (result === null) throw new Error("生成失败：未收到结果");
    return result;
  }, undefined, signal);
}

export async function ingestQACardToKnowledge(
  sessionId: string,
  content: string,
  options: { signal?: AbortSignal; idempotencyKey?: string } = {},
): Promise<{ ok: boolean; topic: string | null; reason: string }> {
  const res = await authFetch(`${API_BASE}/qa-arena/sessions/${sessionId}/ingest-knowledge`, {
    method: "POST",
    headers: authHeaders({
      "Content-Type": "application/json",
      ...(options.idempotencyKey ? { "Idempotency-Key": options.idempotencyKey } : {}),
    }),
    body: JSON.stringify({ content }),
    signal: options.signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "收录失败");
  }
  return res.json();
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
