"""算法题知识卡片持久化 (SQLite)."""
import json
import uuid
from datetime import datetime

from backend.storage.database import get_db


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags", "[]") or "[]")
    d["conversation_history"] = json.loads(d.get("conversation_history", "[]") or "[]")
    return d


def add_algorithm_card(*, user_id: str, title: str, problem_text: str,
                       difficulty: str = "", tags: list = None,
                       solution: str = "", conversation_history: list = None,
                       source_url: str = "", language: str = "python",
                       note: str = "") -> dict:
    conn = get_db()
    card_id = uuid.uuid4().hex[:12]
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    history_json = json.dumps(conversation_history or [], ensure_ascii=False)
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO algorithm_cards "
        "(id, user_id, title, problem_text, difficulty, tags, solution, "
        "conversation_history, source_url, language, note, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (card_id, user_id, title, problem_text, difficulty, tags_json,
         solution, history_json, source_url, language, note, now, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM algorithm_cards WHERE id = ?", (card_id,)).fetchone()
    return _row_to_dict(row)


def list_algorithm_cards(*, user_id: str, difficulty: str = None,
                         tag: str = None, search: str = None,
                         sort_by: str = "created_at", sort_order: str = "desc",
                         limit: int = 50, offset: int = 0) -> dict:
    conn = get_db()
    where = ["user_id = ?"]
    params: list = [user_id]
    if difficulty:
        where.append("difficulty = ?")
        params.append(difficulty)
    if search:
        where.append("(title LIKE ? OR problem_text LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if tag:
        # tags is a JSON array — membership test in SQL via json_each so the tag
        # filter, LIMIT/OFFSET pagination and total agree. (Was: filter in Python
        # AFTER LIMIT/OFFSET → dropped matches on later pages, wrong total.)
        where.append("EXISTS (SELECT 1 FROM json_each(algorithm_cards.tags) WHERE value = ?)")
        params.append(tag)

    where_sql = " AND ".join(where)

    allowed_sort = {"created_at", "updated_at", "difficulty", "title"}
    if sort_by not in allowed_sort:
        sort_by = "created_at"
    order = "DESC" if sort_order.lower() == "desc" else "ASC"

    total = conn.execute(
        f"SELECT COUNT(*) FROM algorithm_cards WHERE {where_sql}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM algorithm_cards WHERE {where_sql} ORDER BY {sort_by} {order} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return {"items": [_row_to_dict(r) for r in rows], "total": total}


def get_algorithm_card(card_id: str, *, user_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM algorithm_cards WHERE id = ? AND user_id = ?",
        (card_id, user_id),
    ).fetchone()
    return _row_to_dict(row) if row else None


def update_algorithm_card(card_id: str, *, user_id: str, **kwargs) -> bool:
    conn = get_db()
    updates = []
    params = []
    for key in ("title", "difficulty", "note", "solution", "source_url", "language"):
        if key in kwargs and kwargs[key] is not None:
            updates.append(f"{key} = ?")
            params.append(kwargs[key])
    if "tags" in kwargs and kwargs["tags"] is not None:
        updates.append("tags = ?")
        params.append(json.dumps(kwargs["tags"], ensure_ascii=False))
    if not updates:
        return False
    updates.append("updated_at = ?")
    params.append(datetime.now().isoformat())
    params.extend([card_id, user_id])
    cursor = conn.execute(
        f"UPDATE algorithm_cards SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        params,
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_algorithm_card(card_id: str, *, user_id: str) -> bool:
    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM algorithm_cards WHERE id = ? AND user_id = ?",
        (card_id, user_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_algorithm_tags(*, user_id: str) -> list[str]:
    conn = get_db()
    rows = conn.execute(
        "SELECT tags FROM algorithm_cards WHERE user_id = ?", (user_id,)
    ).fetchall()
    tag_set = set()
    for r in rows:
        for t in json.loads(r["tags"] or "[]"):
            tag_set.add(t)
    return sorted(tag_set)


def export_algorithm_cards(*, user_id: str, ids: list = None,
                           difficulty: str = None, fmt: str = "json") -> str:
    conn = get_db()
    where = ["user_id = ?"]
    params: list = [user_id]
    if ids:
        placeholders = ",".join("?" * len(ids))
        where.append(f"id IN ({placeholders})")
        params.extend(ids)
    if difficulty:
        where.append("difficulty = ?")
        params.append(difficulty)

    rows = conn.execute(
        f"SELECT * FROM algorithm_cards WHERE {' AND '.join(where)} ORDER BY created_at DESC",
        params,
    ).fetchall()

    items = [_row_to_dict(r) for r in rows]

    if fmt == "markdown":
        return _to_markdown(items)
    return json.dumps(items, ensure_ascii=False, indent=2)


def _to_markdown(items: list[dict]) -> str:
    diff_map = {"easy": "简单", "medium": "中等", "hard": "困难"}
    lines = [
        "# SparkOffer 算法题收藏导出",
        f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"共 {len(items)} 题",
        "",
        "---",
        "",
    ]
    for i, it in enumerate(items, 1):
        lines.append(f"## {i}. {it['title']}")
        lines.append("")
        meta = []
        if it.get("difficulty"):
            meta.append(f"**难度:** {diff_map.get(it['difficulty'], it['difficulty'])}")
        if it.get("language"):
            meta.append(f"**语言:** {it['language']}")
        if it.get("tags"):
            meta.append(f"**标签:** {', '.join(it['tags'])}")
        if it.get("source_url"):
            meta.append(f"**来源:** {it['source_url']}")
        if meta:
            lines.append(" | ".join(meta))
            lines.append("")
        if it.get("problem_text"):
            lines.append("### 题目描述")
            lines.append(it["problem_text"])
            lines.append("")
        if it.get("solution"):
            lines.append("### 解答")
            lines.append(it["solution"])
            lines.append("")
        if it.get("note"):
            lines.append("### 笔记")
            lines.append(it["note"])
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)
