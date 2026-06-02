"""收藏夹持久化 (SQLite)."""
import json
import uuid
from datetime import datetime

from backend.storage.database import get_db


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags", "[]") or "[]")
    return d


def add_favorite(*, user_id: str, session_id: str = None, question: str,
                 user_answer: str = "", reference_answer: str = "",
                 score: float = None, assessment: str = "",
                 topic: str = "", difficulty: str = "", tags: list = None) -> dict:
    conn = get_db()
    fav_id = uuid.uuid4().hex[:12]
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    conn.execute(
        "INSERT INTO favorites (id, user_id, session_id, question, user_answer, "
        "reference_answer, score, assessment, topic, difficulty, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fav_id, user_id, session_id, question, user_answer,
         reference_answer, score, assessment, topic, difficulty, tags_json),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM favorites WHERE id = ?", (fav_id,)).fetchone()
    return _row_to_dict(row)


def list_favorites(*, user_id: str, topic: str = None, tag: str = None,
                   sort_by: str = "created_at", sort_order: str = "desc",
                   limit: int = 50, offset: int = 0) -> dict:
    conn = get_db()
    where = ["user_id = ?"]
    params: list = [user_id]
    if topic:
        where.append("topic = ?")
        params.append(topic)
    if tag:
        # tags is a JSON array — test membership in SQL via json_each so the tag
        # filter, LIMIT/OFFSET pagination and total all agree. (Was: filter in
        # Python AFTER LIMIT/OFFSET, which dropped matches on later pages and
        # reported the per-page count as the grand total.)
        where.append("EXISTS (SELECT 1 FROM json_each(favorites.tags) WHERE value = ?)")
        params.append(tag)

    where_sql = " AND ".join(where)

    allowed_sort = {"created_at", "score", "topic", "difficulty"}
    if sort_by not in allowed_sort:
        sort_by = "created_at"
    order = "DESC" if sort_order.lower() == "desc" else "ASC"

    total = conn.execute(f"SELECT COUNT(*) FROM favorites WHERE {where_sql}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM favorites WHERE {where_sql} ORDER BY {sort_by} {order} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return {"items": [_row_to_dict(r) for r in rows], "total": total}


def update_favorite(fav_id: str, *, user_id: str, tags: list = None, note: str = None) -> bool:
    conn = get_db()
    updates = []
    params = []
    if tags is not None:
        updates.append("tags = ?")
        params.append(json.dumps(tags, ensure_ascii=False))
    if note is not None:
        updates.append("note = ?")
        params.append(note)
    if not updates:
        return False
    params.extend([fav_id, user_id])
    cursor = conn.execute(
        f"UPDATE favorites SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        params,
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_favorite(fav_id: str, *, user_id: str) -> bool:
    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM favorites WHERE id = ? AND user_id = ?",
        (fav_id, user_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_favorite_tags(*, user_id: str) -> list[str]:
    conn = get_db()
    rows = conn.execute(
        "SELECT tags FROM favorites WHERE user_id = ?", (user_id,)
    ).fetchall()
    tag_set = set()
    for r in rows:
        for t in json.loads(r["tags"] or "[]"):
            tag_set.add(t)
    return sorted(tag_set)


def export_favorites(*, user_id: str, ids: list = None,
                     topic: str = None, fmt: str = "json") -> str:
    conn = get_db()
    where = ["user_id = ?"]
    params: list = [user_id]
    if ids:
        placeholders = ",".join("?" * len(ids))
        where.append(f"id IN ({placeholders})")
        params.extend(ids)
    if topic:
        where.append("topic = ?")
        params.append(topic)

    rows = conn.execute(
        f"SELECT * FROM favorites WHERE {' AND '.join(where)} ORDER BY created_at DESC",
        params,
    ).fetchall()

    items = [_row_to_dict(r) for r in rows]

    if fmt == "markdown":
        return _to_markdown(items)
    return json.dumps(items, ensure_ascii=False, indent=2)


def _to_markdown(items: list[dict]) -> str:
    lines = [
        "# SparkOffer 收藏夹导出",
        f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"共 {len(items)} 题",
        "",
        "---",
        "",
    ]
    for i, it in enumerate(items, 1):
        lines.append(f"## {i}. {it['question']}")
        lines.append("")
        meta = []
        if it.get("topic"):
            meta.append(f"**领域:** {it['topic']}")
        if it.get("score") is not None:
            meta.append(f"**分数:** {it['score']}/10")
        if it.get("difficulty"):
            meta.append(f"**难度:** {it['difficulty']}")
        if it.get("tags"):
            meta.append(f"**标签:** {', '.join(it['tags'])}")
        if meta:
            lines.append(" | ".join(meta))
            lines.append("")
        if it.get("user_answer"):
            lines.append("### 我的回答")
            lines.append(it["user_answer"])
            lines.append("")
        if it.get("reference_answer"):
            lines.append("### 参考答案")
            lines.append(it["reference_answer"])
            lines.append("")
        if it.get("assessment"):
            lines.append("### 点评")
            lines.append(it["assessment"])
            lines.append("")
        if it.get("note"):
            lines.append("### 笔记")
            lines.append(it["note"])
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)
