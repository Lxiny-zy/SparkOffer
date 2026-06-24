import { useEffect, useRef } from "react";

interface UseDraftPersistOptions<T> {
  /** Stable storage key. When null/empty, persistence is disabled. */
  key: string | null;
  /** Current value to persist. */
  value: T;
  /** Called once on mount (or when key changes) with a restored draft, if any. */
  onRestore?: (restored: T) => void;
  /** Debounce window in ms before writing to localStorage. Default 400. */
  debounceMs?: number;
  /** Skip persisting when this returns true (e.g. value is empty). */
  isEmpty?: (value: T) => boolean;
  /** Max age of a draft in ms; older drafts are ignored on restore. Default 7 days. */
  maxAgeMs?: number;
}

interface StoredDraft<T> {
  value: T;
  savedAt: number;
}

const DEFAULT_MAX_AGE = 7 * 24 * 60 * 60 * 1000;

/**
 * Synchronously read a persisted draft, or null if absent/expired/corrupt.
 * Use when a page fetches server content asynchronously and must reconcile it
 * with a saved draft itself (avoiding a race with the hook's mount restore).
 */
export function readDraft<T>(key: string | null, maxAgeMs = DEFAULT_MAX_AGE): T | null {
  if (!key) return null;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredDraft<T>;
    if (typeof parsed?.savedAt === "number" && Date.now() - parsed.savedAt > maxAgeMs) {
      localStorage.removeItem(key);
      return null;
    }
    return parsed && "value" in parsed ? parsed.value : null;
  } catch {
    return null;
  }
}

/**
 * Debounced localStorage persistence for editor/input drafts.
 *
 * Mirrors the two-tier resilience pattern in Interview.tsx: writes are
 * synchronous and never fail on network issues, so an accidental refresh,
 * tab close, or crash no longer discards in-progress work.
 *
 * Usage:
 *   const { clear } = useDraftPersist({
 *     key: sessionId ? `qa:draft:${sessionId}` : null,
 *     value: input,
 *     onRestore: (text) => setInput(text),
 *     isEmpty: (t) => !t.trim(),
 *   });
 *   // call clear() after a successful submit/save
 */
export default function useDraftPersist<T>({
  key,
  value,
  onRestore,
  debounceMs = 400,
  isEmpty,
  maxAgeMs = DEFAULT_MAX_AGE,
}: UseDraftPersistOptions<T>) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const valueRef = useRef(value);
  valueRef.current = value;
  const skipInitialSaveKeyRef = useRef<string | null>(null);

  const cancelPendingSave = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const clear = () => {
    if (!key) return;
    cancelPendingSave();
    try {
      localStorage.removeItem(key);
    } catch {
      /* noop */
    }
  };

  // Restore once per key, and skip the first save for that key so previous
  // screen/session state cannot be copied into the new draft slot.
  useEffect(() => {
    cancelPendingSave();
    skipInitialSaveKeyRef.current = key;
    if (!key) return;
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return;
      const parsed = JSON.parse(raw) as StoredDraft<T>;
      if (typeof parsed?.savedAt === "number" && Date.now() - parsed.savedAt > maxAgeMs) {
        localStorage.removeItem(key);
        return;
      }
      if (parsed && "value" in parsed) {
        onRestore?.(parsed.value);
      }
    } catch {
      /* corrupt draft: ignore */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // Debounced save on value change.
  useEffect(() => {
    if (!key) return;
    if (skipInitialSaveKeyRef.current === key) {
      skipInitialSaveKeyRef.current = null;
      return;
    }
    cancelPendingSave();
    timerRef.current = setTimeout(() => {
      const current = valueRef.current;
      if (isEmpty?.(current)) {
        try {
          localStorage.removeItem(key);
        } catch {
          /* noop */
        }
        return;
      }
      try {
        localStorage.setItem(key, JSON.stringify({ value: current, savedAt: Date.now() } as StoredDraft<T>));
      } catch {
        /* quota / serialization failure: non-fatal */
      }
    }, debounceMs);

    return () => {
      cancelPendingSave();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, value, debounceMs]);

  return { clear };
}
