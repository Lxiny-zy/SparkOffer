import { API_BASE, authHeaders } from "./client";

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

  if (res.status === 401) {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    window.location.href = "/login";
    return;
  }

  if (!res.ok) throw new Error(`问答演练场错误: ${res.status}`);

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          yield JSON.parse(line.slice(6));
        } catch {
          // skip malformed
        }
      }
    }
  }
}

// ── Summary ──

export async function generateQASummary(
  sessionId: string,
  onProgress?: (msg: string) => void,
): Promise<QASummaryResult> {
  const res = await fetch(`${API_BASE}/qa-arena/sessions/${sessionId}/summary`, {
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

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: QASummaryResult | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const event = JSON.parse(line.slice(6));
        if (event.type === "progress" && onProgress) {
          onProgress(event.message);
        } else if (event.type === "error") {
          throw new Error(event.message);
        } else if (event.type === "complete") {
          result = event.data;
        }
      } catch (e: any) {
        if (e.message && !e.message.includes("JSON")) throw e;
      }
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
