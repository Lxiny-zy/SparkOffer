"""audit_logs repository — append security events, query for the admin panel.

Writers must keep `detail` free of secrets: log channel names / model ids /
field names, never API keys or passwords.
"""
import json

from backend.storage.database import get_db

# Known event names (informal registry — new writers should extend this list):
#   login_success, login_failed, login_rate_limited,
#   register_success, register_blocked, register_rate_limited,
#   password_changed, password_change_failed, profile_updated,
#   ai_config_updated, channels_updated, tuning_updated


def log_event(event: str, *, user_id: str = None, email: str = None,
              ip: str = None, detail: dict | str = ""):
    if isinstance(detail, dict):
        detail = json.dumps(detail, ensure_ascii=False)
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_logs (event, user_id, email, ip, detail) VALUES (?, ?, ?, ?, ?)",
        (event, user_id, email, ip, detail),
    )
    conn.commit()


def list_audit_logs(*, event: str = None, user_id: str = None,
                    limit: int = 100, offset: int = 0) -> dict:
    conn = get_db()
    where = ["1=1"]
    params: list = []
    if event:
        where.append("event = ?")
        params.append(event)
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    where_sql = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) FROM audit_logs WHERE {where_sql}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM audit_logs WHERE {where_sql} "
        "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        params + [min(int(limit), 500), int(offset)],
    ).fetchall()
    return {"total": total, "items": [dict(r) for r in rows]}


def list_event_names() -> list[str]:
    conn = get_db()
    rows = conn.execute("SELECT DISTINCT event FROM audit_logs ORDER BY event").fetchall()
    return [r["event"] for r in rows]
