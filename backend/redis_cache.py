"""Redis-backed cache with in-memory LRU fallback.

The cache is intentionally optional: when REDIS_URL is unset or Redis is
unreachable, every operation transparently falls back to a bounded in-process
LRU. Callers never need to handle Redis-specific exceptions.

Used by:
- embedding cache (Phase 3 / Phase 5B difficulty anchors)
- prompt fragment cache (Phase 7)
- topic→subtopic cache (Phase 3)
- seed pool inverted index (Phase 7c)

Embedding payloads use a small header so a model swap (different dim) is
caught on read rather than silently returning garbage to downstream cosine.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import struct
import threading
from collections import OrderedDict
from typing import Any, Optional

import numpy as np

logger = logging.getLogger("uvicorn")

_LRU_MAX = 5000
_EMBED_TTL = 7 * 24 * 3600   # 7d
_DEFAULT_JSON_TTL = 3600     # 1h

# Embedding payload header:
#   bytes [0:4] = magic b'EMB1'   (bumpable on schema changes)
#   bytes [4:8] = dim   (uint32 little-endian)
#   bytes [8:]  = raw float32 little-endian vector
# Validating the header on read catches the "different embedding model was
# active when this entry was written" case without trusting the raw bytes.
_EMBED_MAGIC = b"EMB1"
_EMBED_HEADER_LEN = 8


class _LRU:
    """Tiny thread-safe LRU used as the fallback when Redis is absent."""

    def __init__(self, capacity: int = _LRU_MAX) -> None:
        self._cap = capacity
        self._data: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[bytes]:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key: str, value: bytes) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self._cap:
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class _Stats:
    __slots__ = ("hits", "misses", "errors")

    def __init__(self) -> None:
        self.hits = 0
        self.misses = 0
        self.errors = 0


class RedisCache:
    """Singleton facade. Real Redis if configured, LRU fallback otherwise."""

    def __init__(self) -> None:
        self._redis = None
        self._lru = _LRU()
        self.stats = _Stats()
        self._lock = threading.Lock()
        self._url: str = ""

    def init(self, url: str) -> None:
        """Try to connect. Safe to call multiple times — a failed retry does
        not nuke a previously-working client."""
        if not url:
            # Only log the "no URL" message once at first init.
            if self._redis is None and not self._url:
                logger.info("Redis cache: no REDIS_URL set, using in-memory LRU (capacity=%d)", _LRU_MAX)
            self._url = ""
            return
        try:
            import redis  # type: ignore
            client = redis.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
            client.ping()
            # Only swap on success — preserves a working client if a later
            # init() call hits a misconfigured URL.
            self._redis = client
            self._url = url
            logger.info("Redis cache: connected to %s", url)
        except Exception as exc:
            logger.warning(
                "Redis cache: connection to %s failed (%s). Keeping existing backend (%s).",
                url, exc, "redis" if self._redis is not None else "memory",
            )

    def is_ready(self) -> bool:
        return self._redis is not None

    # ── low-level bytes get/set ──

    def _get(self, key: str) -> Optional[bytes]:
        if self._redis is not None:
            try:
                return self._redis.get(key)
            except Exception as exc:
                self.stats.errors += 1
                logger.warning("Redis GET failed (%s); falling back to LRU once", exc)
        return self._lru.get(key)

    def _set(self, key: str, value: bytes, ttl: int) -> None:
        if self._redis is not None:
            try:
                self._redis.set(key, value, ex=ttl)
                return
            except Exception as exc:
                self.stats.errors += 1
                logger.warning("Redis SET failed (%s); falling back to LRU once", exc)
        self._lru.set(key, value)

    # ── async wrappers (offload sync redis-py calls to a thread) ──

    async def aget(self, key: str) -> Optional[bytes]:
        return await asyncio.to_thread(self._get, key)

    async def aset(self, key: str, value: bytes, ttl: int) -> None:
        await asyncio.to_thread(self._set, key, value, ttl)

    async def aget_embedding(self, text: str) -> Optional[np.ndarray]:
        return await asyncio.to_thread(self.get_embedding, text)

    async def aset_embedding(self, text: str, vector: np.ndarray, ttl: int = _EMBED_TTL) -> None:
        await asyncio.to_thread(self.set_embedding, text, vector, ttl)

    # ── high-level helpers ──

    def get_json(self, key: str) -> Any:
        raw = self._get(key)
        if raw is None:
            self.stats.misses += 1
            return None
        try:
            self.stats.hits += 1
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            self.stats.errors += 1
            return None

    def set_json(self, key: str, value: Any, ttl: int = _DEFAULT_JSON_TTL) -> None:
        try:
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            logger.warning("Redis set_json: payload not serializable for key=%s (%s)", key, exc)
            return
        self._set(key, payload, ttl)

    def get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Returns cached embedding as float32 ndarray, or None on miss.

        Validates the header so an entry written by a different embedding
        model (different dim, different schema) is reported as miss instead
        of silently returning a misshaped vector to the caller.
        """
        key = self._embed_key(text)
        raw = self._get(key)
        if raw is None or len(raw) < _EMBED_HEADER_LEN:
            self.stats.misses += 1
            return None
        try:
            magic = bytes(raw[:4])
            if magic != _EMBED_MAGIC:
                # Old / foreign payload — treat as miss; SET will overwrite.
                self.stats.misses += 1
                return None
            dim = struct.unpack("<I", raw[4:8])[0]
            payload = raw[_EMBED_HEADER_LEN:]
            if dim == 0 or len(payload) != dim * 4:
                self.stats.misses += 1
                return None
            self.stats.hits += 1
            return np.frombuffer(payload, dtype=np.float32).copy()
        except Exception:
            self.stats.errors += 1
            return None

    def set_embedding(self, text: str, vector: np.ndarray, ttl: int = _EMBED_TTL) -> None:
        try:
            arr = np.asarray(vector, dtype=np.float32)
        except (TypeError, ValueError):
            return
        if arr.ndim != 1 or arr.size == 0:
            return
        header = _EMBED_MAGIC + struct.pack("<I", int(arr.size))
        self._set(self._embed_key(text), header + arr.tobytes(), ttl)

    def delete(self, key: str) -> None:
        if self._redis is not None:
            try:
                self._redis.delete(key)
                return
            except Exception as exc:
                logger.warning("Redis DEL failed (%s)", exc)
        self._lru.delete(key)

    # ── key helpers ──

    @staticmethod
    def _embed_key(text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        return f"embed:{digest}"

    def health(self) -> dict:
        return {
            "backend": "redis" if self._redis is not None else "memory",
            "url": self._url if self._redis is not None else "",
            "hits": self.stats.hits,
            "misses": self.stats.misses,
            "errors": self.stats.errors,
        }


_instance: RedisCache | None = None


def get_cache() -> RedisCache:
    global _instance
    if _instance is None:
        _instance = RedisCache()
    return _instance


def init_cache(url: str) -> RedisCache:
    cache = get_cache()
    cache.init(url)
    return cache
