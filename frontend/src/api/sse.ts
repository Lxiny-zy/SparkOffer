import { authHeaders } from "./client";

export interface SSECallbacks {
  onProgress?: (message: string) => void;
}

/**
 * Fetch an endpoint that may return SSE or plain JSON.
 * - If response is `text/event-stream`: parse SSE events, call callbacks,
 *   return the `complete` event's data.
 * - Otherwise: fall back to `res.json()` (backward compat / cache hits).
 */
export async function fetchSSE<T>(
  url: string,
  options: RequestInit,
  callbacks?: SSECallbacks,
): Promise<T> {
  const res = await fetch(url, {
    ...options,
    headers: {
      ...authHeaders(
        options.headers instanceof Headers
          ? Object.fromEntries(options.headers.entries())
          : (options.headers as Record<string, string>) || {},
      ),
    },
  });

  if (res.status === 401) {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || err.message || `请求失败 (${res.status})`);
  }

  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("text/event-stream")) {
    return res.json();
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: T | null = null;

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
        switch (event.type) {
          case "progress":
            callbacks?.onProgress?.(event.message);
            break;
          case "error":
            throw new Error(event.message);
          case "complete":
            result = event.data as T;
            break;
        }
      } catch (e: any) {
        if (e.message && !e.message.includes("JSON")) throw e;
      }
    }
  }

  if (!result) throw new Error("请求失败：未收到结果");
  return result;
}
