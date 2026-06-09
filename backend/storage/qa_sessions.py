"""问答演练场会话与消息持久化 (SQLite)."""

import uuid
from datetime import datetime, timezone

from backend.storage.database import get_db


def create_session(user_id: str, title: str = "新对话") -> dict:
    conn = get_db()
    sid = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO qa_sessions (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (sid, user_id, title, now, now),
    )
    conn.commit()
    return {"id": sid, "user_id": user_id, "title": title, "created_at": now, "updated_at": now}


def list_sessions(user_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, created_at, updated_at FROM qa_sessions "
        "WHERE user_id = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def count_sessions(user_id: str) -> int:
    conn = get_db()
    return conn.execute(
        "SELECT COUNT(*) FROM qa_sessions WHERE user_id = ?", (user_id,)
    ).fetchone()[0]


def get_session(session_id: str, user_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT id, user_id, title, created_at, updated_at FROM qa_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def update_session_title(session_id: str, user_id: str, title: str) -> bool:
    conn = get_db()
    cur = conn.execute(
        "UPDATE qa_sessions SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (title, datetime.now(timezone.utc).isoformat(), session_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_session(session_id: str, user_id: str) -> bool:
    conn = get_db()
    conn.execute("DELETE FROM qa_messages WHERE session_id = ? AND user_id = ?", (session_id, user_id))
    cur = conn.execute("DELETE FROM qa_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def save_message(session_id: str, user_id: str, role: str, content: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO qa_messages (session_id, user_id, role, content) VALUES (?, ?, ?, ?)",
        (session_id, user_id, role, content),
    )
    conn.execute(
        "UPDATE qa_sessions SET updated_at = ? WHERE id = ? AND user_id = ?",
        (datetime.now(timezone.utc).isoformat(), session_id, user_id),
    )
    conn.commit()


def load_messages(session_id: str, user_id: str, limit: int = 100) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content, created_at FROM qa_messages "
        "WHERE session_id = ? AND user_id = ? ORDER BY id ASC LIMIT ?",
        (session_id, user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def clear_messages(session_id: str, user_id: str) -> bool:
    conn = get_db()
    conn.execute("DELETE FROM qa_messages WHERE session_id = ? AND user_id = ?", (session_id, user_id))
    conn.commit()
    return True


def delete_last_message_if_assistant(session_id: str, user_id: str) -> bool:
    """Delete the most recent message iff it is an assistant turn.

    Used by the regenerate flow to drop a broken/partial/empty AI reply before
    re-answering the same user question — without touching the user message.
    Returns True if a row was deleted.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT id, role FROM qa_messages WHERE session_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1",
        (session_id, user_id),
    ).fetchone()
    if not row or row["role"] != "assistant":
        return False
    conn.execute("DELETE FROM qa_messages WHERE id = ?", (row["id"],))
    conn.commit()
    return True


def message_count(session_id: str, user_id: str) -> int:
    conn = get_db()
    return conn.execute(
        "SELECT COUNT(*) FROM qa_messages WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()[0]


def get_context_summary(session_id: str, user_id: str) -> tuple[str, int] | None:
    """Return (summary_text, summary_msg_count) or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT context_summary, summary_msg_count FROM qa_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if row and row["context_summary"]:
        return row["context_summary"], row["summary_msg_count"] or 0
    return None


def save_context_summary(session_id: str, user_id: str, summary: str, msg_count: int):
    conn = get_db()
    conn.execute(
        "UPDATE qa_sessions SET context_summary = ?, summary_msg_count = ? WHERE id = ? AND user_id = ?",
        (summary, msg_count, session_id, user_id),
    )
    conn.commit()
