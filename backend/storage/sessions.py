"""面试记录持久化 (SQLite)."""
import json
from datetime import datetime

from backend.storage.database import get_db


def create_session(session_id: str, mode: str, topic: str | None = None,
                   questions: list | None = None, meta: dict | None = None, *, user_id: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO sessions (session_id, mode, topic, meta, questions, user_id) VALUES (?, ?, ?, ?, ?, ?)",
        (
            session_id,
            mode,
            topic,
            json.dumps(meta or {}, ensure_ascii=False),
            json.dumps(questions or [], ensure_ascii=False),
            user_id,
        ),
    )
    conn.commit()


def append_message(session_id: str, role: str, content: str, *, user_id: str):
    """Append a message to transcript using SQLite JSON function (no full reload)."""
    conn = get_db()
    now = datetime.now().isoformat()
    msg_json = json.dumps({"role": role, "content": content, "time": now}, ensure_ascii=False)
    conn.execute(
        "UPDATE sessions SET transcript = json_insert(COALESCE(transcript, '[]'), '$[#]', json(?)), "
        "updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ?",
        (msg_json, session_id, user_id),
    )
    conn.commit()


def save_drill_answers(session_id: str, answers: list[dict], *, user_id: str):
    """Save drill answers into transcript as Q&A pairs."""
    conn = get_db()
    row = conn.execute(
        "SELECT questions FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        return
    questions = json.loads(row["questions"])
    answer_map = {a["question_id"]: a["answer"] for a in answers}

    transcript = []
    for q in questions:
        transcript.append({"role": "assistant", "content": q["question"], "time": datetime.now().isoformat()})
        answer = answer_map.get(q["id"], "")
        if answer:
            transcript.append({"role": "user", "content": answer, "time": datetime.now().isoformat()})

    conn.execute(
        "UPDATE sessions SET transcript = ?, updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ?",
        (json.dumps(transcript, ensure_ascii=False), session_id, user_id),
    )
    conn.commit()


def save_review(session_id: str, review: str, scores: list = None,
                weak_points: list = None, overall: dict = None, *, user_id: str):
    conn = get_db()
    conn.execute(
        "UPDATE sessions SET review = ?, scores = ?, weak_points = ?, overall = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE session_id = ? AND user_id = ?",
        (review, json.dumps(scores or [], ensure_ascii=False),
         json.dumps(weak_points or [], ensure_ascii=False),
         json.dumps(overall or {}, ensure_ascii=False),
         session_id, user_id),
    )
    conn.commit()


def save_drill_progress(session_id: str, current_index: int,
                        partial_answers: dict, hints: dict, *, user_id: str) -> bool:
    """中途保存 drill / job_prep 进度到 meta.progress，不需要 schema 迁移。

    Returns True if the row was updated, False if no such session exists for
    this user — callers should treat False as 404.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT meta FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        return False
    meta = json.loads(row["meta"] or "{}")
    meta["progress"] = {
        "current_index": current_index,
        "partial_answers": {str(k): v for k, v in (partial_answers or {}).items()},
        "hints": {str(k): v for k, v in (hints or {}).items()},
        "updated_at": datetime.now().isoformat(),
    }
    conn.execute(
        "UPDATE sessions SET meta = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE session_id = ? AND user_id = ?",
        (json.dumps(meta, ensure_ascii=False), session_id, user_id),
    )
    conn.commit()
    return True


def get_session(session_id: str, *, user_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["transcript"] = json.loads(result["transcript"])
    result["meta"] = json.loads(result.get("meta", "{}") or "{}")
    result["questions"] = json.loads(result.get("questions", "[]"))
    result["scores"] = json.loads(result["scores"])
    result["weak_points"] = json.loads(result["weak_points"])
    result["overall"] = json.loads(result.get("overall", "{}") or "{}")
    result["reference_answers"] = json.loads(result.get("reference_answers", "{}") or "{}")
    return result


def list_sessions_by_topic(topic: str, *, user_id: str, limit: int = 50) -> list[dict]:
    """Get all sessions for a topic with reviews and scores."""
    conn = get_db()
    rows = conn.execute(
        "SELECT session_id, mode, topic, review, scores, created_at FROM sessions "
        "WHERE topic = ? AND user_id = ? AND review IS NOT NULL ORDER BY created_at ASC LIMIT ?",
        (topic, user_id, limit),
    ).fetchall()
    results = []
    for r in rows:
        results.append({
            "session_id": r["session_id"],
            "review": r["review"],
            "scores": json.loads(r["scores"]) if r["scores"] else [],
            "created_at": r["created_at"],
        })
    return results


def list_sessions(
    *, user_id: str,
    limit: int = 20,
    offset: int = 0,
    mode: str | None = None,
    topic: str | None = None,
    status: str = "completed",
) -> dict:
    conn = get_db()

    if status == "in_progress":
        where = ["review IS NULL", "user_id = ?"]
    elif status == "all":
        where = ["user_id = ?"]
    else:
        where = ["review IS NOT NULL", "user_id = ?"]
    params: list = [user_id]
    if mode:
        where.append("mode = ?")
        params.append(mode)
    if topic:
        where.append("topic = ?")
        params.append(topic)
    where_sql = " AND ".join(where)

    total = conn.execute(
        f"SELECT COUNT(*) FROM sessions WHERE {where_sql}", params,
    ).fetchone()[0]

    rows = conn.execute(
        f"SELECT session_id, mode, topic, meta, created_at, overall FROM sessions "
        f"WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    items = []
    for r in rows:
        overall = json.loads(r["overall"] or "{}")
        meta = json.loads(r["meta"] or "{}")
        items.append({
            "session_id": r["session_id"],
            "mode": r["mode"],
            "topic": r["topic"],
            "meta": meta,
            "created_at": r["created_at"],
            "avg_score": overall.get("avg_score"),
        })
    return {"items": items, "total": total}


def delete_session(session_id: str, *, user_id: str) -> bool:
    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def list_distinct_topics(*, user_id: str) -> list[str]:
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT topic FROM sessions "
        "WHERE topic IS NOT NULL AND review IS NOT NULL AND user_id = ? ORDER BY topic",
        (user_id,),
    ).fetchall()
    return [r["topic"] for r in rows]


def get_reference_answer(session_id: str, question_id, *, user_id: str) -> str | None:
    conn = get_db()
    row = conn.execute(
        "SELECT reference_answers FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        return None
    answers = json.loads(row["reference_answers"] or "{}")
    return answers.get(str(question_id))


def save_reference_answer(session_id: str, question_id, answer: str, *, user_id: str):
    conn = get_db()
    row = conn.execute(
        "SELECT reference_answers FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        return
    answers = json.loads(row["reference_answers"] or "{}")
    answers[str(question_id)] = answer
    conn.execute(
        "UPDATE sessions SET reference_answers = ?, updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ?",
        (json.dumps(answers, ensure_ascii=False), session_id, user_id),
    )
    conn.commit()
