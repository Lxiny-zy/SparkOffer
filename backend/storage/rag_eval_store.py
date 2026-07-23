"""Persistence for RAG eval runs — one record per benchmark run.

Distinct from rag_metrics_store (per session/stage live gauge): this stores the
run-level RAGAS summary plus the per-question breakdown (in detail_json).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.storage.database import get_db


class RagEvalPersistenceFenceError(RuntimeError):
    """The worker no longer owns the durable evaluation execution lease."""


class RagEvalLeaseLostError(RagEvalPersistenceFenceError):
    """The claim token is stale or its lease is no longer valid."""


class RagEvalRunConflictError(RagEvalPersistenceFenceError):
    """A run for the user/job identity was already persisted."""


RagEvalStartClaim = tuple[str, str, str, str, str]


def claim_rag_eval_start_request(
    user_id: str,
    idempotency_key: str,
    request_hash: str,
    candidate_job_id: str,
    *,
    response: dict | None = None,
    lease_for: timedelta = timedelta(minutes=2),
) -> tuple[str, str, str | None]:
    """Create or replay a durable RAG-eval start identity.

    Returns ``(state, job_id, claim_token)``. Only a newly inserted row owns an
    execution lease; callers must separately reclaim an expired ghost mapping.
    """
    conn = get_db()
    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    claim_token = uuid.uuid4().hex
    lease_expires_at = (now + lease_for).isoformat()
    cur = conn.execute(
        "INSERT OR IGNORE INTO rag_eval_start_requests "
        "(user_id, idempotency_key, request_hash, job_id, response_json, claim_token, "
        "lease_expires_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            idempotency_key,
            request_hash,
            candidate_job_id,
            json.dumps(response or {}, ensure_ascii=False),
            claim_token,
            lease_expires_at,
            now_text,
            now_text,
        ),
    )
    conn.commit()
    if cur.rowcount > 0:
        return "claimed", candidate_job_id, claim_token

    row = conn.execute(
        "SELECT request_hash, job_id FROM rag_eval_start_requests "
        "WHERE user_id = ? AND idempotency_key = ?",
        (user_id, idempotency_key),
    ).fetchone()
    if row is None:
        # Rows are immutable in normal operation. Treat an unexpected concurrent
        # removal as retryable instead of executing without a durable identity.
        return "pending", candidate_job_id, None
    if row["request_hash"] != request_hash:
        return "conflict", str(row["job_id"]), None
    return "replay", str(row["job_id"]), None


def get_rag_eval_start_request(
    user_id: str,
    idempotency_key: str,
) -> dict[str, Any] | None:
    """Read a durable start mapping without acquiring its execution lease."""
    conn = get_db()
    row = conn.execute(
        "SELECT request_hash, job_id, response_json, claim_token, lease_expires_at "
        "FROM rag_eval_start_requests "
        "WHERE user_id = ? AND idempotency_key = ?",
        (user_id, idempotency_key),
    ).fetchone()
    return _start_request_row_to_dict(row) if row is not None else None


def get_rag_eval_start_request_by_job(
    job_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Find a durable start mapping for status polling on another worker."""
    conn = get_db()
    row = conn.execute(
        "SELECT request_hash, job_id, response_json, claim_token, lease_expires_at "
        "FROM rag_eval_start_requests "
        "WHERE user_id = ? AND job_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id, job_id),
    ).fetchone()
    return _start_request_row_to_dict(row) if row is not None else None


def _start_request_row_to_dict(row) -> dict[str, Any]:
    try:
        response = json.loads(row["response_json"] or "{}")
    except (TypeError, ValueError):
        response = {}
    now_text = datetime.now(timezone.utc).isoformat()
    return {
        "request_hash": str(row["request_hash"]),
        "job_id": str(row["job_id"]),
        "claim_token": str(row["claim_token"]),
        "response": response if isinstance(response, dict) else {},
        "lease_active": str(row["lease_expires_at"]) > now_text,
    }


def reclaim_rag_eval_start_request(
    user_id: str,
    idempotency_key: str,
    request_hash: str,
    job_id: str,
    *,
    response: dict | None = None,
    lease_for: timedelta = timedelta(minutes=2),
) -> str | None:
    """Take an expired execution lease while preserving the mapped job id."""
    conn = get_db()
    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    claim_token = uuid.uuid4().hex
    response_json = (
        json.dumps(response, ensure_ascii=False) if response is not None else None
    )
    cur = conn.execute(
        "UPDATE rag_eval_start_requests "
        "SET claim_token = ?, lease_expires_at = ?, updated_at = ?, "
        "response_json = COALESCE(?, response_json) "
        "WHERE user_id = ? AND idempotency_key = ? AND request_hash = ? "
        "AND job_id = ? AND lease_expires_at <= ? "
        "AND NOT EXISTS (SELECT 1 FROM rag_eval_runs "
        "WHERE user_id = ? AND job_id = ?)",
        (
            claim_token,
            (now + lease_for).isoformat(),
            now_text,
            response_json,
            user_id,
            idempotency_key,
            request_hash,
            job_id,
            now_text,
            user_id,
            job_id,
        ),
    )
    conn.commit()
    return claim_token if cur.rowcount > 0 else None


def renew_rag_eval_start_request(
    user_id: str,
    idempotency_key: str,
    request_hash: str,
    job_id: str,
    claim_token: str,
    *,
    lease_for: timedelta = timedelta(minutes=2),
) -> bool:
    """Extend the lease held by the worker executing the benchmark."""
    conn = get_db()
    now = datetime.now(timezone.utc)
    cur = conn.execute(
        "UPDATE rag_eval_start_requests "
        "SET lease_expires_at = ?, updated_at = ? "
        "WHERE user_id = ? AND idempotency_key = ? AND request_hash = ? "
        "AND job_id = ? AND claim_token = ? AND lease_expires_at > ?",
        (
            (now + lease_for).isoformat(),
            now.isoformat(),
            user_id,
            idempotency_key,
            request_hash,
            job_id,
            claim_token,
            now.isoformat(),
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def expire_rag_eval_start_request(
    user_id: str,
    idempotency_key: str,
    request_hash: str,
    job_id: str,
    claim_token: str,
) -> bool:
    """Release a lease and rotate its token so a late renewal cannot revive it."""
    conn = get_db()
    now_text = datetime.now(timezone.utc).isoformat()
    released_token = uuid.uuid4().hex
    cur = conn.execute(
        "UPDATE rag_eval_start_requests "
        "SET claim_token = ?, lease_expires_at = ?, updated_at = ? "
        "WHERE user_id = ? AND idempotency_key = ? AND request_hash = ? "
        "AND job_id = ? AND claim_token = ?",
        (
            released_token,
            now_text,
            now_text,
            user_id,
            idempotency_key,
            request_hash,
            job_id,
            claim_token,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


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
    durable_claim: RagEvalStartClaim | None = None,
) -> int:
    conn = get_db()
    persisted_claim_token: str | None = None
    fence_params: tuple[Any, ...] = ()
    now_text = datetime.now(timezone.utc).isoformat()
    if durable_claim is not None:
        claim_user, claim_key, claim_hash, claim_job, claim_token = durable_claim
        if claim_user != user_id or claim_job != job_id:
            raise RagEvalLeaseLostError("RAG eval claim does not match the run")
        persisted_claim_token = claim_token
        fence_params = (
            claim_user,
            claim_key,
            claim_hash,
            claim_job,
            claim_token,
            now_text,
        )

    columns = (
        "job_id", "user_id", "topic", "scope", "n_questions", "k", "judge_mode",
        "eval_kind", "retrieval_mode", "dataset_id", "dataset_version",
        "dataset_hash", "corpus_hash", "seed", "claim_token",
        "hit_at_k", "hit_at_k_strict", "mrr", "ndcg_at_k", "context_precision",
        "context_recall", "faithfulness", "answer_relevancy", "answer_correctness",
        "success_rate", "latency_p50_ms", "latency_p95_ms", "status", "error",
        "manifest_json", "detail_json",
    )
    values: tuple[Any, ...] = (
        job_id, user_id, topic, scope, n_questions, k, judge_mode,
        eval_kind, retrieval_mode, dataset_id, dataset_version,
        dataset_hash, corpus_hash, seed, persisted_claim_token,
        hit_at_k, hit_at_k_strict, mrr, ndcg_at_k, context_precision, context_recall,
        faithfulness, answer_relevancy, answer_correctness,
        success_rate, latency_p50_ms, latency_p95_ms,
        status, error,
        json.dumps(manifest or {}, ensure_ascii=False),
        json.dumps(detail or {}, ensure_ascii=False),
    )
    placeholders = ", ".join("?" for _ in values)
    sql = f"INSERT INTO rag_eval_runs ({', '.join(columns)}) "
    if durable_claim is None:
        # A durable job may never fall back to the legacy unfenced insert path.
        # This makes the store fail closed if a current or future caller forgets
        # to propagate the claim token.
        sql += (
            f"SELECT {placeholders} WHERE NOT EXISTS ("
            "SELECT 1 FROM rag_eval_start_requests "
            "WHERE user_id = ? AND job_id = ?"
            ")"
        )
        params = values + (user_id, job_id)
    else:
        # A single INSERT ... SELECT makes the token check and result write one
        # SQLite statement. A reclaiming worker cannot change the token between
        # validation and insertion, and an expired lease is fenced as well.
        sql += (
            f"SELECT {placeholders} WHERE EXISTS ("
            "SELECT 1 FROM rag_eval_start_requests "
            "WHERE user_id = ? AND idempotency_key = ? AND request_hash = ? "
            "AND job_id = ? AND claim_token = ? AND lease_expires_at > ?"
            ")"
        )
        params = values + fence_params
    try:
        cur = conn.execute(sql, params)
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        existing = conn.execute(
            "SELECT 1 FROM rag_eval_runs WHERE user_id = ? AND job_id = ? LIMIT 1",
            (user_id, job_id),
        ).fetchone()
        if existing is not None:
            raise RagEvalRunConflictError(
                f"RAG eval run already exists for {user_id}/{job_id}"
            ) from exc
        raise
    conn.commit()
    if cur.rowcount == 0:
        raise RagEvalLeaseLostError(
            f"A valid RAG eval claim is required for {user_id}/{job_id}"
        )
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
    durable_claim: RagEvalStartClaim | None = None,
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
        durable_claim=durable_claim,
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
