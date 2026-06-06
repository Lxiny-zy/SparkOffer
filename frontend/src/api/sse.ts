import { authHeaders, handleStreamUnauthorized, iterSSEFrames } from "./client";

export interface SSECallbacks {
  onProgress?: (message: string) => void;
  /** 真流式：后端以 stream_content 模式推送 content 事件时，每段增量回调一次。 */
  onContent?: (delta: string) => void;
}

// SSE 请求超时时间（毫秒）
// 6 分钟硬上限。出题流水线最坏情况：retrieve(90s) + generate(70s) +
// validate+repair(60s) + 余量。早先用 120s 在复杂出题/网络抖动时会误杀
// 正常请求，让用户看到 "network error"。后端有 30s SSE heartbeat 兜底真正的卡死。
const SSE_TIMEOUT_MS = 360000;

/** Map an AbortController abort into a localized 超时 error; pass other errors through. */
function _timeoutError(error: any, timeoutMs: number): Error {
  return error?.name === "AbortError"
    ? new Error(`请求超时（${timeoutMs / 1000}秒）`)
    : error;
}

/**
 * Run an SSE fetch under the shared hard timeout: wires an AbortController,
 * maps an abort into the localized 超时 message, and always clears the timer.
 * `fn` receives the signal to hand to fetch(). For consumers that *yield* frames,
 * use withSSETimeoutGen instead.
 */
export async function withSSETimeout<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  timeoutMs = SSE_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fn(controller.signal);
  } catch (error: any) {
    throw _timeoutError(error, timeoutMs);
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Generator form of withSSETimeout: delegates to an inner async generator with
 * `yield*`, so frames propagate to the caller under the same abort/timeout/cleanup
 * wrapper. Use for streaming consumers that yield SSE events.
 */
export async function* withSSETimeoutGen<T>(
  fn: (signal: AbortSignal) => AsyncGenerator<T>,
  timeoutMs = SSE_TIMEOUT_MS,
  externalSignal?: AbortSignal,
): AsyncGenerator<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  // Let a caller-supplied signal (e.g. component unmount) also abort the fetch
  // so the stream/connection is released immediately rather than on next chunk.
  const onExternalAbort = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener("abort", onExternalAbort);
  }
  try {
    yield* fn(controller.signal);
  } catch (error: any) {
    throw _timeoutError(error, timeoutMs);
  } finally {
    clearTimeout(timeoutId);
    externalSignal?.removeEventListener("abort", onExternalAbort);
  }
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
  return withSSETimeout<T>(async (signal) => {
    const res = await fetch(url, {
      ...options,
      signal,
      headers: {
        ...authHeaders(
          options.headers instanceof Headers
            ? Object.fromEntries(options.headers.entries())
            : (options.headers as Record<string, string>) || {},
        ),
      },
    });

    if (handleStreamUnauthorized(res)) {
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

    let result: T | null = null;

    for await (const event of iterSSEFrames(res)) {
      switch (event.type) {
        case "progress":
          callbacks?.onProgress?.(event.message);
          break;
        case "content":
          callbacks?.onContent?.(event.delta);
          break;
        case "error":
          throw new Error(event.message);
        case "complete":
          result = event.data as T;
          break;
      }
    }

    if (!result) throw new Error("请求失败：未收到结果");
    return result;
  });
}
