"""Process-wide persistent LangGraph checkpointer.

Replaces the per-graph in-memory ``MemorySaver`` so an in-flight resume
interview survives a server restart and can be served by any worker: graph
state is keyed by ``thread_id`` (= session_id) in ``data/checkpoints.db``.

``graph.invoke`` runs inside an ``asyncio.to_thread`` worker (see
``utils/sse_helpers.stream_blocking_sse``), so the sqlite3 connection is opened
with ``check_same_thread=False`` and WAL mode for safe cross-thread access.
``SqliteSaver`` serializes its own reads/writes with an internal lock, so the
single shared connection is safe under the project's low resume-concurrency.
"""
import logging
import sqlite3
import threading

from backend.config import settings

logger = logging.getLogger("uvicorn")

_saver = None
_lock = threading.Lock()


def get_checkpointer():
    """Return the process-wide ``SqliteSaver`` singleton (lazy-initialized)."""
    global _saver
    if _saver is not None:
        return _saver
    with _lock:
        if _saver is not None:
            return _saver
        from langgraph.checkpoint.sqlite import SqliteSaver

        settings.checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(settings.checkpoint_db_path),
            check_same_thread=False,  # invoke() runs in to_thread workers
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        saver = SqliteSaver(conn)
        saver.setup()  # create checkpoint tables once
        _saver = saver
        logger.info("LangGraph SqliteSaver initialized at %s", settings.checkpoint_db_path)
        return _saver
