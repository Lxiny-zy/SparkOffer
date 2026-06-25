"""知识训练卡片持久化 + SM-2 复习调度 (SQLite).

知识训练场生成的卡片落库于此。每张卡的 id 是确定性 kt-hash（topic|title|来源），
所以重复生成同一张卡是 INSERT OR IGNORE 的空操作（精确去重）。熟悉度标记驱动
SM-2 间隔重复：到期（next_review <= 今天）的卡进入「到期复习」队列，遗忘曲线由此生效。
"""
import json
from datetime import date

from backend.storage.database import get_db
from backend.spaced_repetition import sm2_update

# 三档熟悉度 → 0-10 分，喂给 SM-2（quality = min(5, score//2)）：
#   已掌握 → 9（quality 4，间隔拉长）
#   模糊   → 6（quality 3，勉强 pass，短间隔）
#   不会   → 2（quality 1，判错，重置到 1 天）
FAMILIARITY_SCORE = {"known": 9.0, "uncertain": 6.0, "unknown": 2.0}


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["source_refs"] = json.loads(d.get("source_refs") or "[]")
    d["sr_state"] = json.loads(d.get("sr_state") or "{}")
    return d


def _first_ref(source_refs: list) -> tuple[str, str]:
    if source_refs and isinstance(source_refs[0], dict):
        ref = source_refs[0]
        return (ref.get("filename", "") or "", ref.get("header_path", "") or "")
    return ("", "")


def upsert_cards(user_id: str, topic: str, depth: str, cards: list[dict]) -> list[dict]:
    """Persist generated cards (exact dedup via PRIMARY KEY). Existing cards keep
    their SRS state untouched. Returns the stored rows for all given ids, so the
    caller can return cards with their current familiarity / due state."""
    if not cards:
        return []
    conn = get_db()
    ids = []
    for card in cards:
        cid = card.get("id")
        if not cid:
            continue
        ids.append(cid)
        src_file, src_header = _first_ref(card.get("source_refs") or [])
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_cards "
            "(id, user_id, topic, title, knowledge, example, question, answer, "
            " tags, source_refs, source_file, source_header, depth) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid, user_id, topic,
                card.get("title", ""), card.get("knowledge", ""),
                card.get("example", ""), card.get("question", ""), card.get("answer", ""),
                json.dumps(card.get("tags") or [], ensure_ascii=False),
                json.dumps(card.get("source_refs") or [], ensure_ascii=False),
                src_file, src_header, depth,
            ),
        )
    conn.commit()
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM knowledge_cards WHERE user_id = ? AND id IN ({placeholders})",
        [user_id, *ids],
    ).fetchall()
    by_id = {r["id"]: _row_to_dict(r) for r in rows}
    # Preserve the generation order of `cards`.
    return [by_id[cid] for cid in ids if cid in by_id]


def list_cards(
    *, user_id: str, topic: str | None = None, familiarity: str | None = None,
    due_only: bool = False, limit: int = 200, offset: int = 0,
) -> dict:
    conn = get_db()
    where = ["user_id = ?"]
    params: list = [user_id]
    if topic:
        where.append("topic = ?")
        params.append(topic)
    if familiarity:
        where.append("familiarity = ?")
        params.append(familiarity)
    if due_only:
        where.append("next_review IS NOT NULL AND next_review <= ?")
        params.append(date.today().isoformat())
    where_sql = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) FROM knowledge_cards WHERE {where_sql}", params
    ).fetchone()[0]
    # Due cards first by due date; otherwise newest first.
    order = "next_review ASC" if due_only else "created_at DESC"
    rows = conn.execute(
        f"SELECT * FROM knowledge_cards WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return {"items": [_row_to_dict(r) for r in rows], "total": total}


def get_due_cards(*, user_id: str, topic: str | None = None, limit: int = 50) -> list[dict]:
    return list_cards(user_id=user_id, topic=topic, due_only=True, limit=limit)["items"]


def record_review(*, user_id: str, card_id: str, familiarity: str) -> dict | None:
    """Apply a familiarity mark to a card: update SM-2 state + next_review.
    Returns the updated card dict, or None if the card doesn't exist."""
    if familiarity not in FAMILIARITY_SCORE:
        raise ValueError(f"unknown familiarity: {familiarity}")
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM knowledge_cards WHERE user_id = ? AND id = ?", (user_id, card_id)
    ).fetchone()
    if row is None:
        return None
    sr_state = json.loads(row["sr_state"] or "{}")
    new_sr = sm2_update(sr_state, FAMILIARITY_SCORE[familiarity])
    conn.execute(
        "UPDATE knowledge_cards SET familiarity = ?, sr_state = ?, next_review = ?, "
        "review_count = review_count + 1, last_reviewed_at = CURRENT_TIMESTAMP, "
        "updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND id = ?",
        (
            familiarity, json.dumps(new_sr, ensure_ascii=False),
            new_sr.get("next_review"), user_id, card_id,
        ),
    )
    conn.commit()
    updated = conn.execute(
        "SELECT * FROM knowledge_cards WHERE user_id = ? AND id = ?", (user_id, card_id)
    ).fetchone()
    return _row_to_dict(updated)


def covered_section_keys(user_id: str, topic: str) -> set[tuple[str, str]]:
    """(filename, header_path) of sections already turned into cards for this topic.
    The generator uses this to steer sampling toward un-carded knowledge."""
    conn = get_db()
    rows = conn.execute(
        "SELECT source_file, source_header FROM knowledge_cards WHERE user_id = ? AND topic = ?",
        (user_id, topic),
    ).fetchall()
    return {(r["source_file"] or "", r["source_header"] or "") for r in rows}


def topic_due_counts(user_id: str) -> dict[str, int]:
    """Per-topic count of cards due for review today — for the availability badges."""
    conn = get_db()
    rows = conn.execute(
        "SELECT topic, COUNT(*) AS n FROM knowledge_cards "
        "WHERE user_id = ? AND next_review IS NOT NULL AND next_review <= ? "
        "GROUP BY topic",
        (user_id, date.today().isoformat()),
    ).fetchall()
    return {r["topic"]: r["n"] for r in rows}


def topic_saved_counts(user_id: str) -> dict[str, int]:
    """Per-topic count of saved cards — for the availability badges."""
    conn = get_db()
    rows = conn.execute(
        "SELECT topic, COUNT(*) AS n FROM knowledge_cards WHERE user_id = ? GROUP BY topic",
        (user_id,),
    ).fetchall()
    return {r["topic"]: r["n"] for r in rows}


def delete_card(*, user_id: str, card_id: str) -> bool:
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM knowledge_cards WHERE user_id = ? AND id = ?", (user_id, card_id)
    )
    conn.commit()
    return cur.rowcount > 0
