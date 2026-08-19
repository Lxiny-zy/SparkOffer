import { API_BASE, authHeaders, handleStreamUnauthorized, iterSSEFrames } from "./client";
import { SSETerminalError, withSSETimeoutGen } from "./sse";
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
export function streamAssistantChat(message: string, signal?: AbortSignal): AsyncGenerator<any> {
  return withSSETimeoutGen(async function* (innerSignal) {
    const res = await fetch(`${API_BASE}/assistant/chat`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ message }),
      signal: innerSignal,
    });

    if (handleStreamUnauthorized(res)) {
      return;
    }

    if (!res.ok) {
      throw new Error(`Assistant error: ${res.status}`);
    }

    let sawDone = false;
    let errorMessage: string | null = null;
    for await (const event of iterSSEFrames(res)) {
      if (event.type === "error") {
        errorMessage = event.message || "AI 服务暂时不可用，请稍后重试";
        continue;
      }
      if (event.type === "done") {
        sawDone = true;
        if (event.terminal === "error" && !errorMessage) {
          errorMessage = "AI 服务暂时不可用，请稍后重试";
        }
        continue;
      }
      yield event;
    }
    if (errorMessage) throw new SSETerminalError(errorMessage, "error");
    if (!sawDone) {
      throw new SSETerminalError("连接提前关闭，未收到完成事件，请重试");
    }
  }, undefined, signal);
}
