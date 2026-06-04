"""Persistence for RAG eval runs — one record per benchmark run.

Distinct from rag_metrics_store (per session/stage live gauge): this stores the
run-level RAGAS summary plus the per-question breakdown (in detail_json).
"""
from __future__ import annotations

import json
from typing import Any

from backend.storage.database import get_db


def save_rag_eval_run(
    *,
    job_id: str,
    user_id: str,
    topic: str,
    scope: str,
    n_questions: int,
    k: int,
    judge_mode: str,
    hit_at_k: float | None,
    mrr: float | None,
    context_precision: float | None,
    context_recall: float | None,
    faithfulness: float | None,
    answer_relevancy: float | None,
    answer_correctness: float | None,
    status: str,
    error: str = "",
    detail: dict | None = None,
) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO rag_eval_runs
           (job_id, user_id, topic, scope, n_questions, k, judge_mode,
            hit_at_k, mrr, context_precision, context_recall,
            faithfulness, answer_relevancy, answer_correctness,
            status, error, detail_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job_id, user_id, topic, scope, n_questions, k, judge_mode,
            hit_at_k, mrr, context_precision, context_recall,
            faithfulness, answer_relevancy, answer_correctness,
            status, error,
            json.dumps(detail or {}, ensure_ascii=False),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_rag_eval_runs(
    user_id: str,
    topic: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    conn = get_db()
    clauses = ["user_id = ?"]
    params: list[Any] = [user_id]
    if topic:
        clauses.append("topic = ?")
        params.append(topic)
    where = " AND ".join(clauses)
    params.extend([limit, offset])

    rows = conn.execute(
        f"""SELECT id, job_id, topic, scope, n_questions, k, judge_mode,
                   hit_at_k, mrr, context_precision, context_recall,
                   faithfulness, answer_relevancy, answer_correctness,
                   status, error, detail_json, created_at
            FROM rag_eval_runs
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?""",
        params,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_rag_eval_run(run_id: int, user_id: str) -> dict[str, Any] | None:
    conn = get_db()
    row = conn.execute(
        """SELECT id, job_id, topic, scope, n_questions, k, judge_mode,
                  hit_at_k, mrr, context_precision, context_recall,
                  faithfulness, answer_relevancy, answer_correctness,
                  status, error, detail_json, created_at
           FROM rag_eval_runs
           WHERE id = ? AND user_id = ?""",
        (run_id, user_id),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _row_to_dict(row) -> dict[str, Any]:
    d = dict(row)
    detail_raw = d.pop("detail_json", "{}")
    try:
        d["detail"] = json.loads(detail_raw) if detail_raw else {}
    except (json.JSONDecodeError, TypeError):
        d["detail"] = {}
    return d
