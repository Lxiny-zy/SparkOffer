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


def delete_thread_checkpoints(thread_id: str) -> None:
    """Delete every LangGraph checkpoint row owned by one session id."""
    if not thread_id or not settings.checkpoint_db_path.exists():
        return
    conn = sqlite3.connect(str(settings.checkpoint_db_path))
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("BEGIN IMMEDIATE")
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        for (table_name,) in tables:
            columns = {
                row[1]
                for row in conn.execute(
                    f'PRAGMA table_info("{table_name.replace(chr(34), chr(34) * 2)}")'
                )
            }
            if "thread_id" not in columns:
                continue
            quoted = table_name.replace('"', '""')
            conn.execute(
                f'DELETE FROM "{quoted}" WHERE thread_id = ?', (thread_id,)
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
