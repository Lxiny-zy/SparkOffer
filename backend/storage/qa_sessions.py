"""问答演练场会话与消息持久化 (SQLite)."""

import json
import uuid
from datetime import datetime, timedelta, timezone

from backend.storage.database import get_db
from backend.storage.sessions import new_session_id


INGEST_LEASE_TIMEOUT = timedelta(minutes=15)


def _row_to_message(row) -> dict:
    """Map a qa_messages row to a dict, decoding the JSON ``images`` column to a list."""
    d = dict(row)
    raw = d.pop("images", None)
    try:
        d["images"] = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        d["images"] = []
    return d


def create_session(user_id: str, title: str = "新对话") -> dict:
    conn = get_db()
    sid = new_session_id()
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


def delete_session_checked(session_id: str, user_id: str) -> str:
    """Delete a session unless one of its knowledge ingests is still pending."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cutoff = (
            datetime.now(timezone.utc) - INGEST_LEASE_TIMEOUT
        ).isoformat()
        pending = conn.execute(
            "SELECT 1 FROM qa_ingest_requests "
            "WHERE session_id = ? AND user_id = ? AND status = 'pending' "
            "AND updated_at >= ? LIMIT 1",
            (session_id, user_id, cutoff),
        ).fetchone()
        if pending:
            conn.rollback()
            return "busy"
        conn.execute(
            "DELETE FROM qa_messages WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        )
        conn.execute(
            "DELETE FROM qa_ingest_requests WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        )
        cur = conn.execute(
            "DELETE FROM qa_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        conn.commit()
        return "deleted" if cur.rowcount > 0 else "missing"
    except Exception:
        conn.rollback()
        raise


def delete_session(session_id: str, user_id: str) -> bool:
    """Backward-compatible boolean wrapper around the checked delete result."""
    return delete_session_checked(session_id, user_id) == "deleted"


def claim_ingest_request(
    session_id: str,
    user_id: str,
    idempotency_key: str,
    content_hash: str,
    idempotency_marker: str,
    *,
    stale_after: timedelta = INGEST_LEASE_TIMEOUT,
) -> tuple[str, dict | None, str | None]:
    """Atomically claim a QA-card ingestion request.

    Returns a state, optional cached response, and an ownership token. Pending
    claims can be reclaimed after a bounded lease so a terminated worker cannot
    block the card forever. Only the current token can renew, finish, or release
    the claim.
    """
    conn = get_db()
    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    cutoff = (now - stale_after).isoformat()
    claim_token = uuid.uuid4().hex
    cur = conn.execute(
        "INSERT OR IGNORE INTO qa_ingest_requests "
        "(user_id, session_id, idempotency_key, content_hash, idempotency_marker, "
        "claim_token, status, response_json, created_at, updated_at) "
        "SELECT ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ? "
        "WHERE EXISTS ("
        "SELECT 1 FROM qa_sessions WHERE id = ? AND user_id = ?"
        ")",
        (
            user_id,
            session_id,
            idempotency_key,
            content_hash,
            idempotency_marker,
            claim_token,
            now_text,
            now_text,
            session_id,
            user_id,
        ),
    )
    conn.commit()
    if cur.rowcount > 0:
        return "claimed", None, claim_token

    row = conn.execute(
        "SELECT content_hash, status, response_json FROM qa_ingest_requests "
        "WHERE user_id = ? AND session_id = ? AND idempotency_key = ?",
        (user_id, session_id, idempotency_key),
    ).fetchone()
    if row is None:
        # The row may have been released between INSERT and SELECT. Let the
        # caller retry instead of silently executing without a durable claim.
        session_exists = conn.execute(
            "SELECT 1 FROM qa_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
        return (
            ("pending", None, None)
            if session_exists
            else ("missing", None, None)
        )
    if row["content_hash"] != content_hash:
        return "conflict", None, None
    if row["status"] == "complete" and row["response_json"]:
        try:
            response = json.loads(row["response_json"])
        except (TypeError, ValueError):
            return "conflict", None, None
        return "complete", response if isinstance(response, dict) else None, None

    cur = conn.execute(
        "UPDATE qa_ingest_requests SET claim_token = ?, updated_at = ? "
        "WHERE user_id = ? AND session_id = ? AND idempotency_key = ? "
        "AND content_hash = ? AND status = 'pending' AND updated_at < ?",
        (
            claim_token,
            now_text,
            user_id,
            session_id,
            idempotency_key,
            content_hash,
            cutoff,
        ),
    )
    conn.commit()
    return (
        ("claimed", None, claim_token)
        if cur.rowcount > 0
        else ("pending", None, None)
    )


def get_ingest_plan(
    user_id: str,
    idempotency_marker: str,
    claim_token: str,
) -> tuple[str, str] | None:
    conn = get_db()
    row = conn.execute(
        "SELECT topic_key, normalized_content FROM qa_ingest_requests "
        "WHERE user_id = ? AND idempotency_marker = ? AND claim_token = ? "
        "AND status = 'pending'",
        (user_id, idempotency_marker, claim_token),
    ).fetchone()
    if not row or not row["topic_key"] or not row["normalized_content"]:
        return None
    return row["topic_key"], row["normalized_content"]


def save_ingest_plan(
    user_id: str,
    idempotency_marker: str,
    claim_token: str,
    topic_key: str,
    normalized_content: str,
) -> bool:
    """Persist the post-LLM write plan before touching the knowledge file."""
    conn = get_db()
    cur = conn.execute(
        "UPDATE qa_ingest_requests SET topic_key = ?, normalized_content = ?, "
        "updated_at = ? WHERE user_id = ? AND idempotency_marker = ? "
        "AND claim_token = ? AND status = 'pending' "
        "AND (topic_key IS NULL OR (topic_key = ? AND normalized_content = ?))",
        (
            topic_key,
            normalized_content,
            datetime.now(timezone.utc).isoformat(),
            user_id,
            idempotency_marker,
            claim_token,
            topic_key,
            normalized_content,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def renew_ingest_request(
    session_id: str,
    user_id: str,
    idempotency_key: str,
    content_hash: str,
    claim_token: str,
) -> bool:
    """Refresh an active claim lease, scoped to its current owner."""
    conn = get_db()
    cur = conn.execute(
        "UPDATE qa_ingest_requests SET updated_at = ? "
        "WHERE user_id = ? AND session_id = ? AND idempotency_key = ? "
        "AND content_hash = ? AND claim_token = ? AND status = 'pending'",
        (
            datetime.now(timezone.utc).isoformat(),
            user_id,
            session_id,
            idempotency_key,
            content_hash,
            claim_token,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def complete_ingest_request(
    session_id: str,
    user_id: str,
    idempotency_key: str,
    content_hash: str,
    claim_token: str,
    response: dict,
) -> bool:
    conn = get_db()
    cur = conn.execute(
        "UPDATE qa_ingest_requests SET status = 'complete', response_json = ?, updated_at = ? "
        "WHERE user_id = ? AND session_id = ? AND idempotency_key = ? "
        "AND content_hash = ? AND claim_token = ? AND status = 'pending'",
        (
            json.dumps(response, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
            user_id,
            session_id,
            idempotency_key,
            content_hash,
            claim_token,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def release_ingest_request(
    session_id: str,
    user_id: str,
    idempotency_key: str,
    content_hash: str,
    claim_token: str,
) -> bool:
    """Release an unsuccessful claim so an explicit retry can run again."""
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM qa_ingest_requests "
        "WHERE user_id = ? AND session_id = ? AND idempotency_key = ? "
        "AND content_hash = ? AND claim_token = ? AND status = 'pending'",
        (user_id, session_id, idempotency_key, content_hash, claim_token),
    )
    conn.commit()
    return cur.rowcount > 0


def abandon_ingest_request(
    session_id: str,
    user_id: str,
    idempotency_key: str,
    content_hash: str,
    claim_token: str,
) -> bool:
    """Release pre-plan failures, or expire a durable plan for immediate retry."""
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM qa_ingest_requests "
        "WHERE user_id = ? AND session_id = ? AND idempotency_key = ? "
        "AND content_hash = ? AND claim_token = ? AND status = 'pending' "
        "AND topic_key IS NULL",
        (user_id, session_id, idempotency_key, content_hash, claim_token),
    )
    if cur.rowcount == 0:
        cur = conn.execute(
            "UPDATE qa_ingest_requests SET updated_at = ? "
            "WHERE user_id = ? AND session_id = ? AND idempotency_key = ? "
            "AND content_hash = ? AND claim_token = ? AND status = 'pending'",
            (
                "1970-01-01T00:00:00+00:00",
                user_id,
                session_id,
                idempotency_key,
                content_hash,
                claim_token,
            ),
        )
    conn.commit()
    return cur.rowcount > 0


def save_message(session_id: str, user_id: str, role: str, content: str, images: list[str] | None = None):
    conn = get_db()
    images_json = json.dumps(images) if images else None
    conn.execute(
        "INSERT INTO qa_messages (session_id, user_id, role, content, images) VALUES (?, ?, ?, ?, ?)",
        (session_id, user_id, role, content, images_json),
    )
    conn.execute(
        "UPDATE qa_sessions SET updated_at = ? WHERE id = ? AND user_id = ?",
        (datetime.now(timezone.utc).isoformat(), session_id, user_id),
    )
    conn.commit()


def load_messages(session_id: str, user_id: str, limit: int | None = 100) -> list[dict]:
    """Return messages in chronological order.

    With a limit, returns the MOST RECENT ``limit`` messages (still oldest→newest
    within the window) — a plain ``ORDER BY id ASC LIMIT`` would silently pin the
    window to the oldest messages and drop everything recent. ``limit=None``
    returns the full history (the chat/summary paths need it: the rolling-summary
    ``covered`` cursor indexes from message 0).

    Each message carries an ``images`` list (stored attachment filenames; empty
    when the turn had none).
    """
    conn = get_db()
    if limit is None:
        rows = conn.execute(
            "SELECT role, content, images, created_at FROM qa_messages "
            "WHERE session_id = ? AND user_id = ? ORDER BY id ASC",
            (session_id, user_id),
        ).fetchall()
        return [_row_to_message(r) for r in rows]
    rows = conn.execute(
        "SELECT role, content, images, created_at FROM qa_messages "
        "WHERE session_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, user_id, limit),
    ).fetchall()
    return [_row_to_message(r) for r in reversed(rows)]


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
