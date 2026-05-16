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
