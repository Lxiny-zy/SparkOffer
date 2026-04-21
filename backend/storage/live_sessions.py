"""进行中的会话持久化 (SQLite)，服务器重启后可恢复。"""
import json
from datetime import datetime, timedelta

from backend.storage.database import get_db


def save_live_session(session_id: str, session_type: str, user_id: str, data: dict):
    conn = get_db()
    now = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO live_sessions (session_id, session_type, user_id, data, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(session_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at""",
        (session_id, session_type, user_id, json.dumps(data, ensure_ascii=False), now, now),
    )
    conn.commit()


def load_live_session(session_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT data FROM live_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row:
        return json.loads(row["data"])
    return None


def delete_live_session(session_id: str):
    conn = get_db()
    conn.execute("DELETE FROM live_sessions WHERE session_id = ?", (session_id,))
    conn.commit()


def cleanup_expired_sessions(max_age_hours: int = 24):
    conn = get_db()
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    conn.execute("DELETE FROM live_sessions WHERE updated_at < ?", (cutoff,))
    conn.commit()
