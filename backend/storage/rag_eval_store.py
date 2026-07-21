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
    hit_at_k_strict: float | None = None,
    mrr: float | None,
    context_precision: float | None,
    context_recall: float | None,
    faithfulness: float | None,
    answer_relevancy: float | None,
    answer_correctness: float | None,
    status: str,
    error: str = "",
    eval_kind: str = "synthetic_e2e",
    retrieval_mode: str = "atomic_dense",
    dataset_id: str = "",
    dataset_version: str = "",
    dataset_hash: str = "",
    corpus_hash: str = "",
    seed: int | None = None,
    ndcg_at_k: float | None = None,
    success_rate: float | None = None,
    latency_p50_ms: float | None = None,
    latency_p95_ms: float | None = None,
    manifest: dict | None = None,
    detail: dict | None = None,
) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO rag_eval_runs
           (job_id, user_id, topic, scope, n_questions, k, judge_mode,
            eval_kind, retrieval_mode, dataset_id, dataset_version,
            dataset_hash, corpus_hash, seed,
            hit_at_k, hit_at_k_strict, mrr, ndcg_at_k, context_precision, context_recall,
            faithfulness, answer_relevancy, answer_correctness,
            success_rate, latency_p50_ms, latency_p95_ms,
            status, error, manifest_json, detail_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job_id, user_id, topic, scope, n_questions, k, judge_mode,
            eval_kind, retrieval_mode, dataset_id, dataset_version,
            dataset_hash, corpus_hash, seed,
            hit_at_k, hit_at_k_strict, mrr, ndcg_at_k, context_precision, context_recall,
            faithfulness, answer_relevancy, answer_correctness,
            success_rate, latency_p50_ms, latency_p95_ms,
            status, error,
            json.dumps(manifest or {}, ensure_ascii=False),
            json.dumps(detail or {}, ensure_ascii=False),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def save_failed_rag_eval_run(
    *,
    job_id: str,
    user_id: str,
    topic: str,
    scope: str,
    n_questions: int,
    k: int,
    judge_mode: str,
    eval_kind: str,
    retrieval_mode: str,
    seed: int | None,
    error: str,
    manifest: dict | None = None,
    detail: dict | None = None,
) -> int:
    """Persist failed attempts so reliability history is not success-only."""
    return save_rag_eval_run(
        job_id=job_id,
        user_id=user_id,
        topic=topic,
        scope=scope,
        n_questions=n_questions,
        k=k,
        judge_mode=judge_mode,
        hit_at_k=None,
        hit_at_k_strict=None,
        mrr=None,
        context_precision=None,
        context_recall=None,
        faithfulness=None,
        answer_relevancy=None,
        answer_correctness=None,
        success_rate=0.0,
        eval_kind=eval_kind,
        retrieval_mode=retrieval_mode,
        seed=seed,
        status="failed",
        error=error[:300],
        manifest=manifest,
        detail=detail,
    )


def list_rag_eval_runs(
    user_id: str,
    topic: str | None = None,
    eval_kind: str | None = None,
    retrieval_mode: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    conn = get_db()
    clauses = ["user_id = ?"]
    params: list[Any] = [user_id]
    if topic:
        clauses.append("topic = ?")
        params.append(topic)
    if eval_kind:
        clauses.append("eval_kind = ?")
        params.append(eval_kind)
    if retrieval_mode:
        clauses.append("retrieval_mode = ?")
        params.append(retrieval_mode)
    where = " AND ".join(clauses)
    params.extend([limit, offset])

    rows = conn.execute(
        f"""SELECT id, job_id, topic, scope, n_questions, k, judge_mode,
                   eval_kind, retrieval_mode, dataset_id, dataset_version,
                   dataset_hash, corpus_hash, seed,
                   hit_at_k, hit_at_k_strict, mrr, ndcg_at_k,
                   context_precision, context_recall,
                   faithfulness, answer_relevancy, answer_correctness,
                   success_rate, latency_p50_ms, latency_p95_ms,
                   status, error, manifest_json, created_at
            FROM rag_eval_runs
            WHERE {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?""",
        params,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_rag_eval_run(run_id: int, user_id: str) -> dict[str, Any] | None:
    conn = get_db()
    row = conn.execute(
        """SELECT id, job_id, topic, scope, n_questions, k, judge_mode,
                  eval_kind, retrieval_mode, dataset_id, dataset_version,
                  dataset_hash, corpus_hash, seed,
                  hit_at_k, hit_at_k_strict, mrr, ndcg_at_k,
                  context_precision, context_recall,
                  faithfulness, answer_relevancy, answer_correctness,
                  success_rate, latency_p50_ms, latency_p95_ms,
                  status, error, manifest_json, detail_json, created_at
           FROM rag_eval_runs
           WHERE id = ? AND user_id = ?""",
        (run_id, user_id),
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_rag_eval_run_by_job(job_id: str, user_id: str) -> dict[str, Any] | None:
    """Find a persisted terminal run for a polling client after restart."""
    conn = get_db()
    row = conn.execute(
        """SELECT id, job_id, topic, scope, n_questions, k, judge_mode,
                  eval_kind, retrieval_mode, dataset_id, dataset_version,
                  dataset_hash, corpus_hash, seed,
                  hit_at_k, hit_at_k_strict, mrr, ndcg_at_k,
                  context_precision, context_recall,
                  faithfulness, answer_relevancy, answer_correctness,
                  success_rate, latency_p50_ms, latency_p95_ms,
                  status, error, manifest_json, detail_json, created_at
           FROM rag_eval_runs
           WHERE job_id = ? AND user_id = ?
           ORDER BY id DESC LIMIT 1""",
        (job_id, user_id),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _row_to_dict(row) -> dict[str, Any]:
    d = dict(row)
    for raw_key, parsed_key in (("manifest_json", "manifest"), ("detail_json", "detail")):
        if raw_key not in d:
            continue
        raw = d.pop(raw_key, "{}")
        try:
            d[parsed_key] = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            d[parsed_key] = {}
    return d
