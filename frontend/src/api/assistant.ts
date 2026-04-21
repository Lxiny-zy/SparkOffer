import { API_BASE, authHeaders } from "./client";
import type { ChatMessage } from "../types/api";

/**
 * Load assistant chat history for the current user.
 */
export async function fetchAssistantHistory(): Promise<ChatMessage[]> {
  const res = await fetch(`${API_BASE}/assistant/history`, {
    headers: authHeaders(),
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.messages || [];
}

/**
 * Clear all assistant chat history for the current user.
 */
export async function clearAssistantHistory(): Promise<void> {
  await fetch(`${API_BASE}/assistant/history`, {
    method: "DELETE",
    headers: authHeaders(),
  });
}

/**
 * Fetch personalized welcome-back message (null if new user).
 */
export async function fetchWelcomeMessage(): Promise<string | null> {
  const res = await fetch(`${API_BASE}/assistant/welcome`, {
    headers: authHeaders(),
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.message || null;
}

/**
 * Stream assistant chat via SSE.
 * Yields parsed events: { type: "token"|"action"|"done", ... }
 */
export async function* streamAssistantChat(message: string): AsyncGenerator<any> {
  const res = await fetch(`${API_BASE}/assistant/chat`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message }),
  });

  if (res.status === 401) {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    window.location.href = "/login";
    return;
  }

  if (!res.ok) {
    throw new Error(`Assistant error: ${res.status}`);
  }

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
