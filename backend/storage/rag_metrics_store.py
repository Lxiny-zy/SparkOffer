"""Persistence for RAG quality metrics — one record per (session, stage)."""
from __future__ import annotations

import json
from typing import Any

from backend.storage.database import get_db


def save_rag_metrics(
    session_id: str,
    user_id: str,
    topic: str,
    stage: str,
    *,
    relevance: float | None = None,
    coverage: float | None = None,
    diversity: float | None = None,
    faithfulness: float | None = None,
    answer_relevance: float | None = None,
    answer_correctness: float | None = None,
    chunk_count: int | None = None,
    detail: dict | None = None,
) -> None:
    # question_gen writes relevance into the legacy context_relevance column (so
    # historical trend charts stay continuous) plus coverage + diversity;
    # answer_eval writes the generation-side columns. Circular precision/recall
    # are no longer written.
    conn = get_db()
    conn.execute(
        """INSERT INTO rag_metrics
           (session_id, user_id, topic, stage,
            context_relevance, coverage, diversity,
            faithfulness, answer_relevance, answer_correctness,
            chunk_count, detail_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id, user_id, topic, stage,
            relevance, coverage, diversity,
            faithfulness, answer_relevance, answer_correctness,
            chunk_count,
            json.dumps(detail or {}, ensure_ascii=False),
        ),
    )
    conn.commit()


def get_rag_metrics_history(
    user_id: str,
    topic: str | None = None,
    stage: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    conn = get_db()
    clauses = ["user_id = ?"]
    params: list[Any] = [user_id]
    if topic:
        clauses.append("topic = ?")
        params.append(topic)
    if stage:
        clauses.append("stage = ?")
        params.append(stage)
    where = " AND ".join(clauses)
    params.extend([limit, offset])

    rows = conn.execute(
        f"""SELECT id, session_id, topic, stage,
                   context_relevance, context_precision, context_recall,
                   discrimination, coverage, diversity,
                   faithfulness, answer_relevance, answer_correctness,
                   chunk_count, detail_json, created_at
            FROM rag_metrics
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?""",
        params,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_rag_metrics_for_session(
    session_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        """SELECT id, session_id, topic, stage,
                  context_relevance, context_precision, context_recall,
                  discrimination, coverage, diversity,
                  faithfulness, answer_relevance, answer_correctness,
                  chunk_count, detail_json, created_at
           FROM rag_metrics
           WHERE session_id = ? AND user_id = ?
           ORDER BY created_at ASC""",
        (session_id, user_id),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row) -> dict[str, Any]:
    d = dict(row)
    detail_raw = d.pop("detail_json", "{}")
    try:
        d["detail"] = json.loads(detail_raw) if detail_raw else {}
    except (json.JSONDecodeError, TypeError):
        d["detail"] = {}
    # question_gen relevance lives in the legacy context_relevance column; expose
    # it under the new name so the frontend reads a single `relevance` field
    # regardless of when the row was written.
    d["relevance"] = d.get("context_relevance")
    return d
