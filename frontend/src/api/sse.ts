import { authHeaders, handleStreamUnauthorized, iterSSEFrames } from "./client";

export interface SSECallbacks {
  onProgress?: (message: string) => void;
  /** 真流式：后端以 stream_content 模式推送 content 事件时，每段增量回调一次。 */
  onContent?: (delta: string) => void;
}

export type SSETerminal = "success" | "error";

/** Error raised when an SSE response closes without a valid terminal pair. */
export class SSETerminalError extends Error {
  readonly terminal: SSETerminal | "incomplete";

  constructor(message: string, terminal: SSETerminal | "incomplete" = "incomplete") {
    super(message);
    this.name = "SSETerminalError";
    this.terminal = terminal;
  }
}

// SSE 请求超时时间（毫秒）
// 12-minute hard ceiling. This is deliberately above the sum of the bounded
// retrieval, validation, and repair stages; liveness between those bounds is
// maintained by SSE heartbeats. The backend still owns the shorter per-stage
// timeouts, so extending this client ceiling cannot make a stuck repair run
// forever.
const SSE_TIMEOUT_MS = 720000;

/** Map an AbortController abort into a localized 超时 error; pass other errors through. */
function _timeoutError(error: any, timeoutMs: number): Error {
  return error?.name === "AbortError"
    ? new Error(`请求超时（${timeoutMs / 1000}秒）`)
    : error;
}

/**
 * Run an SSE fetch under the shared hard timeout: wires an AbortController,
 * maps an abort into the localized 超时 message, and always clears the timer.
 * `fn` receives the signal to hand to fetch(). An optional caller-supplied
 * signal (e.g. component unmount / user cancel) also aborts the fetch.
 * For consumers that *yield* frames, use withSSETimeoutGen instead.
 */
export async function withSSETimeout<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  timeoutMs = SSE_TIMEOUT_MS,
  externalSignal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  let abortReason: "timeout" | "external" | null = null;
  const timeoutId = setTimeout(() => {
    // The first abort source owns the error classification. An external
    // cancellation that arrives after the hard timeout must not turn a real
    // timeout into an AbortError (or vice versa).
    if (abortReason === null) abortReason = "timeout";
    controller.abort();
  }, timeoutMs);
  const onExternalAbort = () => {
    if (abortReason === null) abortReason = "external";
    controller.abort();
  };
  if (externalSignal) {
    if (externalSignal.aborted) onExternalAbort();
    else externalSignal.addEventListener("abort", onExternalAbort);
  }
  try {
    return await fn(controller.signal);
  } catch (error: any) {
    // A caller-initiated abort isn't a timeout — re-throw as-is so callers
    // can recognize it by `name === "AbortError"`.
    if (abortReason === "external") throw error;
    throw _timeoutError(error, timeoutMs);
  } finally {
    clearTimeout(timeoutId);
    externalSignal?.removeEventListener("abort", onExternalAbort);
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
  let abortReason: "timeout" | "external" | null = null;
  const timeoutId = setTimeout(() => {
    if (abortReason === null) abortReason = "timeout";
    controller.abort();
  }, timeoutMs);
  // Let a caller-supplied signal (e.g. component unmount) also abort the fetch
  // so the stream/connection is released immediately rather than on next chunk.
  const onExternalAbort = () => {
    if (abortReason === null) abortReason = "external";
    controller.abort();
  };
  if (externalSignal) {
    if (externalSignal.aborted) onExternalAbort();
    else externalSignal.addEventListener("abort", onExternalAbort);
  }
  try {
    yield* fn(controller.signal);
  } catch (error: any) {
    // Preserve caller cancellation so consumers can distinguish it from the
    // hard request timeout. The first abort source wins if both race.
    if (abortReason === "external") throw error;
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
  externalSignal?: AbortSignal,
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
    let sawDone = false;
    let errorMessage: string | null = null;

    for await (const event of iterSSEFrames(res)) {
      switch (event.type) {
        case "progress":
          callbacks?.onProgress?.(event.message);
          break;
        case "content":
          callbacks?.onContent?.(event.delta);
          break;
        case "error":
          errorMessage = event.message || "请求失败，请稍后重试";
          break;
        case "complete":
          result = event.data as T;
          break;
        case "done":
          sawDone = true;
          if (event.terminal === "error" && !errorMessage) {
            errorMessage = "请求失败，请稍后重试";
          }
          break;
      }
    }

    if (errorMessage) throw new SSETerminalError(errorMessage, "error");
    if (!sawDone) {
      throw new SSETerminalError("连接提前关闭，未收到完成事件，请重试");
    }
    if (result === null) throw new Error("请求失败：未收到结果");
    return result;
  }, SSE_TIMEOUT_MS, externalSignal);
}
