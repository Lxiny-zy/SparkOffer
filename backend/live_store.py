"""Shared in-memory session stores + persistence helpers.

Centralises the four in-memory dicts and the save/get/del helpers
so that every router can import them without circular deps.

Includes TTL-based eviction to prevent unbounded memory growth.
"""
import time
import threading
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from backend.storage.live_sessions import (
    save_live_session, load_live_session, delete_live_session,
)


class TTLDict:
    """Dictionary with per-entry TTL eviction.

    Entries are evicted lazily on access and periodically via cleanup().
    Default TTL: 2 hours.
    """

    def __init__(self, default_ttl: float = 7200.0, max_size: int = 200):
        self._data: dict[str, tuple[float, dict]] = {}  # key -> (expire_time, value)
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._lock = threading.Lock()

    def __setitem__(self, key: str, value: dict):
        with self._lock:
            self._data[key] = (time.time() + self._default_ttl, value)
            # Evict least-recently-used if over max size. Access refreshes the
            # expiry (see __getitem__), so the min-by-expiry entry is the LRU one.
            if len(self._data) > self._max_size:
                self._evict_expired()
                if len(self._data) > self._max_size:
                    lru_key = min(self._data, key=lambda k: self._data[k][0])
                    del self._data[lru_key]

    def __getitem__(self, key: str) -> dict:
        with self._lock:
            if key not in self._data:
                raise KeyError(key)
            expire, value = self._data[key]
            if time.time() > expire:
                del self._data[key]
                raise KeyError(key)
            # Refresh TTL on access: live sessions expire on 2h of INACTIVITY,
            # not 2h after creation — an active interview should stay warm.
            self._data[key] = (time.time() + self._default_ttl, value)
            return value

    def __contains__(self, key: str) -> bool:
        with self._lock:
            if key not in self._data:
                return False
            expire, _ = self._data[key]
            if time.time() > expire:
                del self._data[key]
                return False
            return True

    def pop(self, key: str, default=None):
        with self._lock:
            entry = self._data.pop(key, None)
            if entry is None:
                return default
            _, value = entry
            return value

    def _evict_expired(self):
        """Remove all expired entries (must be called with lock held)."""
        now = time.time()
        expired = [k for k, (exp, _) in self._data.items() if now > exp]
        for k in expired:
            del self._data[k]

    def cleanup(self):
        """Public cleanup method for periodic eviction."""
        with self._lock:
            self._evict_expired()


# Session stores with TTL (2 hours for active sessions)
graphs: TTLDict = TTLDict(default_ttl=7200.0, max_size=100)
drill_sessions: TTLDict = TTLDict(default_ttl=7200.0, max_size=200)
job_prep_sessions: TTLDict = TTLDict(default_ttl=7200.0, max_size=200)
algorithm_sessions: TTLDict = TTLDict(default_ttl=7200.0, max_size=200)

# RAG-eval background-job progress (transient; polled by the dashboard). Not
# persisted to SQLite — the durable result lands in the rag_eval_runs table.
rag_eval_jobs: TTLDict = TTLDict(default_ttl=7200.0, max_size=200)

_MSG_TYPE_MAP = {"human": HumanMessage, "ai": AIMessage, "system": SystemMessage}


def save_live(store: dict, session_id: str, session_type: str, user_id: str, data: dict):
    store[session_id] = data
    persist_data = dict(data)
    if session_type == "algorithm" and "messages" in persist_data:
        persist_data["messages"] = [
            {"type": getattr(m, "type", "unknown"), "content": m.content}
            for m in persist_data["messages"]
        ]
    save_live_session(session_id, session_type, user_id, persist_data)


def get_live(store: dict, session_id: str, session_type: str, user_id: str):
    if session_id in store:
        return store[session_id]
    # Scope the persistent fallback to this store's type: the live_sessions table
    # is shared across modes, so a type-blind read would return another mode's row
    # (e.g. a resume row surfacing in the drill branch → KeyError on 'questions').
    data = load_live_session(session_id, user_id, session_type)
    if data is None:
        return None
    if session_type == "algorithm" and "messages" in data:
        data["messages"] = [
            _MSG_TYPE_MAP.get(m["type"], HumanMessage)(content=m["content"])
            for m in data["messages"]
        ]
    store[session_id] = data
    return data


def del_live(store: dict, session_id: str, user_id: str):
    store.pop(session_id, None)
    delete_live_session(session_id, user_id)
