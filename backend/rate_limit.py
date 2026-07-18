"""In-memory sliding-window rate limiter for the auth endpoints.

Per-process only (matches the single-worker deployment). Two usage patterns:
  - attempt-based (register): check_and_record() counts every call.
  - failure-based (login): allow() checks, record_failure() counts only
    failures, reset() clears on success so legitimate users aren't locked
    out by their own successful logins.
"""
import threading
import time
from collections import deque

_LOCK = threading.Lock()
_BUCKETS: dict[str, deque] = {}
_MAX_BUCKETS = 10_000  # memory backstop; prune expired entries when exceeded


def _prune_locked(window: float):
    now = time.monotonic()
    dead = [k for k, dq in _BUCKETS.items() if not dq or now - dq[-1] > window]
    for k in dead:
        del _BUCKETS[k]


def allow(key: str, max_attempts: int, window_seconds: float) -> bool:
    """True if `key` has fewer than max_attempts recorded events in the window."""
    now = time.monotonic()
    with _LOCK:
        dq = _BUCKETS.get(key)
        if not dq:
            return True
        while dq and now - dq[0] > window_seconds:
            dq.popleft()
        return len(dq) < max_attempts


def record_failure(key: str, window_seconds: float = 900):
    now = time.monotonic()
    with _LOCK:
        if len(_BUCKETS) > _MAX_BUCKETS:
            _prune_locked(window_seconds)
        _BUCKETS.setdefault(key, deque()).append(now)


def reset(key: str):
    with _LOCK:
        _BUCKETS.pop(key, None)


def check_and_record(key: str, max_attempts: int, window_seconds: float) -> bool:
    """Atomic allow-then-record for attempt-based limits. False = rejected."""
    now = time.monotonic()
    with _LOCK:
        if len(_BUCKETS) > _MAX_BUCKETS:
            _prune_locked(window_seconds)
        dq = _BUCKETS.setdefault(key, deque())
        while dq and now - dq[0] > window_seconds:
            dq.popleft()
        if len(dq) >= max_attempts:
            return False
        dq.append(now)
        return True
