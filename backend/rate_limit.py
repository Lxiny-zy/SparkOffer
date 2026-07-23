"""In-memory sliding-window rate limiter for the auth endpoints.

Per-process only (matches the single-worker deployment). Two usage patterns:
  - attempt-based (register): check_and_record() counts every call.
  - reservation-based (login): reserve_many() counts in-flight requests and
    failures; successful requests release their exact reservation token.
"""
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from uuid import uuid4

_LOCK = threading.Lock()
_MAX_BUCKETS = 10_000


@dataclass
class _Bucket:
    # Events are ``(monotonic timestamp, reservation token)`` pairs. Ordinary
    # counters use ``None``; login reservations carry a unique token so a
    # successful request can release only its own event.
    events: deque[tuple[float, str | None]] = field(default_factory=deque)
    window_seconds: float = 900
    last_seen: float = 0


_BUCKETS: OrderedDict[str, _Bucket] = OrderedDict()


def _trim_locked(bucket: _Bucket, now: float, window_seconds: float) -> None:
    bucket.window_seconds = window_seconds
    while bucket.events and now - bucket.events[0][0] > window_seconds:
        bucket.events.popleft()


def _prune_locked(now: float) -> None:
    for key, bucket in list(_BUCKETS.items()):
        _trim_locked(bucket, now, bucket.window_seconds)
        if not bucket.events:
            del _BUCKETS[key]


def _touch_locked(key: str, bucket: _Bucket, now: float) -> None:
    bucket.last_seen = now
    _BUCKETS.move_to_end(key)


def _get_or_create_locked(key: str, now: float, window_seconds: float) -> _Bucket:
    bucket = _BUCKETS.get(key)
    if bucket is not None:
        _trim_locked(bucket, now, window_seconds)
        _touch_locked(key, bucket, now)
        return bucket

    _prune_locked(now)
    while len(_BUCKETS) >= _MAX_BUCKETS:
        _BUCKETS.popitem(last=False)
    bucket = _Bucket(window_seconds=window_seconds, last_seen=now)
    _BUCKETS[key] = bucket
    return bucket


def allow(key: str, max_attempts: int, window_seconds: float) -> bool:
    """True if `key` has fewer than max_attempts recorded events in the window."""
    now = time.monotonic()
    with _LOCK:
        bucket = _BUCKETS.get(key)
        if bucket is None:
            return True
        _trim_locked(bucket, now, window_seconds)
        if not bucket.events:
            del _BUCKETS[key]
            return True
        _touch_locked(key, bucket, now)
        return len(bucket.events) < max_attempts


def record_failure(key: str, window_seconds: float = 900):
    now = time.monotonic()
    with _LOCK:
        bucket = _get_or_create_locked(key, now, window_seconds)
        bucket.events.append((now, None))


def reset(key: str):
    with _LOCK:
        _BUCKETS.pop(key, None)


def check_and_record(key: str, max_attempts: int, window_seconds: float) -> bool:
    """Atomic allow-then-record for attempt-based limits. False = rejected."""
    now = time.monotonic()
    with _LOCK:
        bucket = _get_or_create_locked(key, now, window_seconds)
        if len(bucket.events) >= max_attempts:
            return False
        bucket.events.append((now, None))
        return True


def reserve_many(
    limits: list[tuple[str, int, float]],
) -> tuple[bool, str | None]:
    """Atomically reserve one event in every bucket and return its token.

    Login uses this while password verification is running.  A successful
    login can then release exactly its own reservation even when other login
    requests reserve the same buckets concurrently.
    """
    if not limits:
        return True, None

    now = time.monotonic()
    with _LOCK:
        # Prune once before resolving all keys. Existing buckets are checked
        # before any capacity eviction so a failed multi-key reservation does
        # not leave partially-created buckets behind.
        _prune_locked(now)
        unique_limits: list[tuple[str, int, float]] = []
        seen_keys: set[str] = set()
        for key, max_attempts, window_seconds in limits:
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique_limits.append((key, max_attempts, window_seconds))

        if not unique_limits or _MAX_BUCKETS <= 0:
            return False, None

        # A non-positive ceiling can never admit a reservation. Check this
        # before creating any new buckets so the all-or-none contract holds.
        if any(max_attempts <= 0 for _, max_attempts, _ in unique_limits):
            return False, None

        requested_keys = {key for key, _, _ in unique_limits}
        new_limits: list[tuple[str, int, float]] = []
        buckets: list[_Bucket] = []
        for key, max_attempts, window_seconds in unique_limits:
            bucket = _BUCKETS.get(key)
            if bucket is None:
                new_limits.append((key, max_attempts, window_seconds))
                continue

            _trim_locked(bucket, now, window_seconds)
            _touch_locked(key, bucket, now)
            if len(bucket.events) >= max_attempts:
                return False, None
            buckets.append(bucket)

        # A reservation cannot be represented if it requires more buckets than
        # the configured memory cap. Do not create a partial reservation.
        if len(unique_limits) > _MAX_BUCKETS:
            return False, None

        required_evictions = max(0, len(_BUCKETS) + len(new_limits) - _MAX_BUCKETS)
        if required_evictions:
            # Never evict a bucket participating in this reservation: callers
            # must be able to release the token from every requested bucket.
            evictable = [key for key in _BUCKETS if key not in requested_keys]
            if len(evictable) < required_evictions:
                return False, None
            for key in evictable[:required_evictions]:
                del _BUCKETS[key]

        for key, _, window_seconds in new_limits:
            bucket = _Bucket(window_seconds=window_seconds, last_seen=now)
            _BUCKETS[key] = bucket
            buckets.append(bucket)

        token = uuid4().hex
        for bucket in buckets:
            bucket.events.append((now, token))
        return True, token


def check_and_record_many(limits: list[tuple[str, int, float]]) -> bool:
    """Atomically reserve one event in every bucket, or none of them.

    Compatibility wrapper for callers that only need a boolean. Callers that
    may later release a reservation should use :func:`reserve_many` instead.
    """
    allowed, _token = reserve_many(limits)
    return allowed


def release_record(key: str, reservation_token: str | None = None) -> None:
    """Release one reserved event from ``key``.

    With a token, only the exact matching reservation is removed. Omitting the
    token preserves the historical behavior of removing the newest event.
    """
    with _LOCK:
        bucket = _BUCKETS.get(key)
        if bucket is None:
            return
        if reservation_token is None:
            if bucket.events:
                bucket.events.pop()
        else:
            # deque has no keyed deletion; search newest-first for the unique
            # token returned by reserve_many.
            for index in range(len(bucket.events) - 1, -1, -1):
                if bucket.events[index][1] == reservation_token:
                    del bucket.events[index]
                    break
        if not bucket.events:
            del _BUCKETS[key]
