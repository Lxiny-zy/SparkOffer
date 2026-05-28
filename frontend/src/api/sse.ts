import { authHeaders } from "./client";

export interface SSECallbacks {
  onProgress?: (message: string) => void;
}

// SSE 请求超时时间（毫秒）
// 6 分钟硬上限。出题流水线最坏情况：retrieve(90s) + generate(70s) +
// validate+repair(60s) + 余量。早先用 120s 在复杂出题/网络抖动时会误杀
// 正常请求，让用户看到 "network error"。后端有 30s SSE heartbeat 兜底真正的卡死。
const SSE_TIMEOUT_MS = 360000;

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
  // 创建 AbortController 用于超时控制
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), SSE_TIMEOUT_MS);

  try {
    const res = await fetch(url, {
      ...options,
      signal: controller.signal,
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
  } catch (error: any) {
    clearTimeout(timeoutId);
    if (error.name === "AbortError") {
      throw new Error(`请求超时（${SSE_TIMEOUT_MS / 1000}秒）`);
    }
    throw error;
  }
}
