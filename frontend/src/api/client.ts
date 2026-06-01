export const API_BASE: string = "/api";

// Session expired event — components can listen to this to show re-login UI
type SessionExpiredListener = () => void;
const _sessionExpiredListeners: SessionExpiredListener[] = [];
let _sessionExpiredFired = false;

export function onSessionExpired(listener: SessionExpiredListener) {
  _sessionExpiredListeners.push(listener);
  return () => {
    const idx = _sessionExpiredListeners.indexOf(listener);
    if (idx >= 0) _sessionExpiredListeners.splice(idx, 1);
  };
}

function _fireSessionExpired() {
  if (_sessionExpiredFired) return; // Only fire once per session
  _sessionExpiredFired = true;
  _sessionExpiredListeners.forEach((fn) => fn());
}

export function resetSessionExpired() {
  _sessionExpiredFired = false;
}

export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = localStorage.getItem("token");
  const headers: Record<string, string> = { ...extra };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

export async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const headers = authHeaders(options.headers as Record<string, string>);
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) {
    // Fire session expired event instead of hard redirect
    // This allows the UI to show a re-login modal without losing page state
    _fireSessionExpired();
    throw new Error("Session expired");
  }
  if (res.status >= 500) {
    throw new Error(`Backend temporarily unavailable (${res.status})`);
  }
  return res;
}

/**
 * Handle a 401 on a streaming response by firing the session-expired event
 * (shows the re-login modal without losing page state). Returns true if the
 * caller should stop (it was a 401), false otherwise. Use this instead of a
 * hard window.location redirect in hand-rolled stream readers.
 */
export function handleStreamUnauthorized(res: Response): boolean {
  if (res.status === 401) {
    _fireSessionExpired();
    return true;
  }
  return false;
}

/**
 * Parse an SSE response body into an async stream of `data:` JSON events.
 * Shared transport primitive for every streaming consumer — yields each parsed
 * event object. Malformed frames are skipped; an empty trailing chunk never throws.
 */
export async function* iterSSEFrames(res: Response): AsyncGenerator<any> {
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
      if (!line.startsWith("data: ")) continue;
      try {
        yield JSON.parse(line.slice(6));
      } catch {
        // skip malformed SSE frame
      }
    }
  }
}
