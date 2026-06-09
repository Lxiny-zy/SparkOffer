"""助手对话历史持久化 (SQLite)."""

from backend.storage.database import get_db


def save_message(user_id: str, role: str, content: str):
    """Save a single chat message. Auto-trims old messages periodically."""
    conn = get_db()
    conn.execute(
        "INSERT INTO assistant_chats (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content),
    )
    # Commit the insert immediately so a later trim failure (e.g. a lock timeout)
    # can never roll back / discard the message we just saved.
    conn.commit()
    # Trim when THIS user crosses the cap. Keying off the global last_insert_rowid
    # meant a low-frequency user could sit above the limit indefinitely. The 220
    # hysteresis (> keep=200) avoids trimming on every insert. COUNT uses idx_ac_user.
    count = conn.execute(
        "SELECT COUNT(*) FROM assistant_chats WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    if count > 220:
        _trim_history(user_id, keep=200)
        conn.commit()


def _trim_history(user_id: str, keep: int = 200):
    """Delete old messages beyond the keep limit for a user."""
    conn = get_db()
    conn.execute(
        "DELETE FROM assistant_chats WHERE user_id = ? AND id NOT IN ("
        "  SELECT id FROM assistant_chats WHERE user_id = ? ORDER BY id DESC LIMIT ?"
        ")",
        (user_id, user_id, keep),
    )


def load_history(user_id: str, limit: int = 50) -> list[dict]:
    """Load the most recent N messages for a user, oldest first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content, created_at FROM assistant_chats "
        "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    # Reverse to chronological order
    return [{"role": r["role"], "content": r["content"], "created_at": r["created_at"]} for r in reversed(rows)]


def clear_history(user_id: str):
    """Delete all chat history for a user."""
    conn = get_db()
    conn.execute("DELETE FROM assistant_chats WHERE user_id = ?", (user_id,))
    conn.commit()
