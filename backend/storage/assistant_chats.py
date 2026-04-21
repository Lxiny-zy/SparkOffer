"""助手对话历史持久化 (SQLite)."""

from backend.storage.database import get_db


def save_message(user_id: str, role: str, content: str):
    """Save a single chat message. Auto-trims old messages periodically."""
    conn = get_db()
    conn.execute(
        "INSERT INTO assistant_chats (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content),
    )
    # Periodic trim: every ~10 inserts, keep only latest 200 messages per user
    last_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    if last_id % 10 == 0:
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
