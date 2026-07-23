"""SQLite compatibility tests for persisted RAG benchmark runs."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import timedelta

import pytest

import backend.storage.database as database
from backend.live_store import rag_eval_jobs
from backend.routers.rag_eval import rag_eval_status
from backend.storage import rag_eval_store
from backend.storage.rag_eval_store import (
    get_rag_eval_run,
    get_rag_eval_run_by_job,
    list_rag_eval_runs,
    save_rag_eval_run,
)


@pytest.fixture
def isolated_db(tmp_path):
    original_path = database.DB_PATH
    original_conn = getattr(database._local, "conn", None)
    database.DB_PATH = tmp_path / "rag-eval.db"
    database._local.conn = None
    try:
        yield database.DB_PATH
    finally:
        temp_conn = getattr(database._local, "conn", None)
        if temp_conn is not None:
            temp_conn.close()
        database.DB_PATH = original_path
        database._local.conn = original_conn


def test_init_migrates_legacy_rag_eval_table_idempotently(isolated_db):
    conn = sqlite3.connect(isolated_db)
    conn.execute(
        """CREATE TABLE rag_eval_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            topic TEXT NOT NULL,
            scope TEXT DEFAULT 'topic',
            n_questions INTEGER NOT NULL,
            k INTEGER NOT NULL,
            judge_mode TEXT DEFAULT 'standard',
            hit_at_k REAL,
            mrr REAL,
            context_precision REAL,
            context_recall REAL,
            faithfulness REAL,
            answer_relevancy REAL,
            answer_correctness REAL,
            status TEXT NOT NULL,
            error TEXT DEFAULT '',
            detail_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.executemany(
        "INSERT INTO rag_eval_runs "
        "(job_id, user_id, topic, n_questions, k, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("duplicate-job", "legacy-user", "python", 1, 8, "failed"),
            ("duplicate-job", "legacy-user", "python", 1, 8, "completed"),
        ],
    )
    conn.commit()
    conn.close()

    database.init_all_tables()
    database.init_all_tables()

    columns = {
        row[1] for row in database.get_db().execute("PRAGMA table_info(rag_eval_runs)")
    }
    assert {
        "eval_kind", "retrieval_mode", "dataset_hash", "corpus_hash",
        "hit_at_k_strict", "ndcg_at_k", "success_rate",
        "latency_p50_ms", "latency_p95_ms", "manifest_json",
        "claim_token",
    } <= columns
    migrated_job_ids = [
        str(row[0])
        for row in database.get_db().execute(
            "SELECT job_id FROM rag_eval_runs "
            "WHERE user_id = 'legacy-user' ORDER BY id"
        )
    ]
    assert migrated_job_ids[-1] == "duplicate-job"
    assert migrated_job_ids[0].startswith("duplicate-job:legacy-duplicate:")
    indexes = {
        str(row[1]): int(row[2])
        for row in database.get_db().execute("PRAGMA index_list(rag_eval_runs)")
    }
    assert indexes["idx_rag_eval_runs_user_job"] == 1


def test_run_list_is_lightweight_and_detail_survives_restart_lookup(isolated_db):
    database.init_all_tables()
    run_id = save_rag_eval_run(
        job_id="job-123",
        user_id="12345678",
        topic="python",
        scope="topic",
        n_questions=9,
        k=8,
        judge_mode="none",
        eval_kind="frozen_retrieval",
        retrieval_mode="production_replay",
        dataset_id="rag-keyword-regression",
        dataset_version="2",
        dataset_hash="dataset-sha",
        corpus_hash="corpus-sha",
        seed=42,
        hit_at_k=0.8,
        hit_at_k_strict=None,
        mrr=0.7,
        ndcg_at_k=0.75,
        context_precision=0.6,
        context_recall=0.9,
        faithfulness=None,
        answer_relevancy=None,
        answer_correctness=None,
        success_rate=1.0,
        latency_p50_ms=12.0,
        latency_p95_ms=20.0,
        status="completed",
        manifest={"git_sha": "abc", "dataset": {"hash": "dataset-sha"}},
        detail={"questions": [{"id": "q1"}]},
    )

    listed = list_rag_eval_runs(
        "12345678", "python", "frozen_retrieval", "production_replay", 20, 0,
    )
    assert len(listed) == 1
    assert "detail" not in listed[0]
    assert listed[0]["manifest"]["git_sha"] == "abc"
    assert listed[0]["ndcg_at_k"] == 0.75

    detail = get_rag_eval_run(run_id, "12345678")
    assert detail["manifest"]["git_sha"] == "abc"
    assert detail["detail"]["questions"][0]["id"] == "q1"
    assert get_rag_eval_run_by_job("job-123", "12345678")["id"] == run_id
    assert get_rag_eval_run_by_job("job-123", "87654321") is None


def test_status_recovery_preserves_strict_comparability(isolated_db):
    database.init_all_tables()
    manifest = {
        "comparison_signature": "strict-signature",
        "state_stable": True,
        "comparison_dimensions": {"execution_profile": "healthy"},
    }
    save_rag_eval_run(
        job_id="job-recovered",
        user_id="12345678",
        topic="python",
        scope="topic",
        n_questions=2,
        k=8,
        judge_mode="none",
        eval_kind="frozen_retrieval",
        retrieval_mode="production_replay",
        dataset_id="rag-keyword-regression",
        dataset_version="2",
        dataset_hash="dataset-sha",
        corpus_hash="corpus-sha",
        seed=42,
        hit_at_k=0.5,
        hit_at_k_strict=None,
        mrr=0.5,
        ndcg_at_k=0.5,
        context_precision=0.5,
        context_recall=0.5,
        faithfulness=None,
        answer_relevancy=None,
        answer_correctness=None,
        success_rate=1.0,
        latency_p50_ms=12.0,
        latency_p95_ms=20.0,
        status="completed",
        manifest=manifest,
        detail={
            "questions": [
                {"id": "q1", "outcome": "ok"},
                {"id": "q2", "outcome": "empty"},
            ],
        },
    )
    rag_eval_jobs.pop("job-recovered", None)

    recovered = asyncio.run(rag_eval_status("job-recovered", "12345678"))

    assert recovered["summary"]["evaluated_questions"] == 2
    assert recovered["summary"]["degraded_count"] == 0
    assert recovered["summary"]["fully_healthy_rate"] == 1.0
    assert recovered["summary"]["valid"] is True
    assert recovered["summary"]["comparable"] is True


def test_legacy_null_success_rate_remains_unknown_on_recovery(isolated_db):
    database.init_all_tables()
    save_rag_eval_run(
        job_id="legacy-job",
        user_id="12345678",
        topic="python",
        scope="topic",
        n_questions=3,
        k=8,
        judge_mode="standard",
        hit_at_k=0.5,
        hit_at_k_strict=None,
        mrr=0.4,
        context_precision=0.3,
        context_recall=0.2,
        faithfulness=0.1,
        answer_relevancy=0.2,
        answer_correctness=0.3,
        success_rate=None,
        status="completed",
        detail={"questions": [{"question": "legacy"}]},
    )
    rag_eval_jobs.pop("legacy-job", None)

    recovered = asyncio.run(rag_eval_status("legacy-job", "12345678"))

    assert recovered["summary"]["success_rate"] is None
    assert recovered["summary"]["evaluated_questions"] is None
    assert recovered["summary"]["error_count"] is None
    assert recovered["summary"]["valid"] is False
    assert recovered["summary"]["comparable"] is False


def test_stale_claim_cannot_persist_and_job_identity_is_unique(isolated_db):
    database.init_all_tables()
    user_id = "fenced-user"
    job_id = "fenced-job"
    request_hash = "request-hash"
    idempotency_key = "rag-eval:fenced"
    state, _, stale_token = rag_eval_store.claim_rag_eval_start_request(
        user_id,
        idempotency_key,
        request_hash,
        job_id,
        lease_for=timedelta(minutes=1),
    )
    assert state == "claimed"
    assert stale_token is not None
    stale_claim = (
        user_id,
        idempotency_key,
        request_hash,
        job_id,
        stale_token,
    )
    assert rag_eval_store.expire_rag_eval_start_request(*stale_claim) is True
    assert rag_eval_store.renew_rag_eval_start_request(*stale_claim) is False
    current_token = rag_eval_store.reclaim_rag_eval_start_request(
        user_id,
        idempotency_key,
        request_hash,
        job_id,
    )
    assert current_token is not None
    current_claim = (
        user_id,
        idempotency_key,
        request_hash,
        job_id,
        current_token,
    )

    def save_with_claim(claim):
        return save_rag_eval_run(
            job_id=job_id,
            user_id=user_id,
            topic="python",
            scope="topic",
            n_questions=1,
            k=8,
            judge_mode="none",
            hit_at_k=1.0,
            mrr=1.0,
            context_precision=1.0,
            context_recall=1.0,
            faithfulness=None,
            answer_relevancy=None,
            answer_correctness=None,
            status="completed",
            durable_claim=claim,
        )

    with pytest.raises(rag_eval_store.RagEvalLeaseLostError):
        save_with_claim(stale_claim)
    with pytest.raises(rag_eval_store.RagEvalLeaseLostError):
        save_with_claim(None)

    run_id = save_with_claim(current_claim)
    saved = database.get_db().execute(
        "SELECT claim_token FROM rag_eval_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert saved["claim_token"] == current_token

    with pytest.raises(rag_eval_store.RagEvalRunConflictError):
        save_with_claim(current_claim)
    count = database.get_db().execute(
        "SELECT COUNT(*) FROM rag_eval_runs WHERE user_id = ? AND job_id = ?",
        (user_id, job_id),
    ).fetchone()[0]
    assert count == 1
    assert rag_eval_store.expire_rag_eval_start_request(*current_claim) is True
    assert rag_eval_store.reclaim_rag_eval_start_request(
        user_id,
        idempotency_key,
        request_hash,
        job_id,
    ) is None


def test_expire_fences_late_renewal_that_already_captured_time(
    isolated_db,
    monkeypatch,
):
    database.init_all_tables()
    state, job_id, claim_token = rag_eval_store.claim_rag_eval_start_request(
        "late-renew-user",
        "rag-eval:late-renew",
        "late-renew-hash",
        "late-renew-job",
    )
    assert state == "claimed"
    assert claim_token is not None
    claim = (
        "late-renew-user",
        "rag-eval:late-renew",
        "late-renew-hash",
        job_id,
        claim_token,
    )
    renew_reached_update = threading.Event()
    allow_renew_update = threading.Event()
    renew_result: list[bool] = []
    renew_errors: list[BaseException] = []
    original_get_db = rag_eval_store.get_db

    class DelayedRenewConnection:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            if "AND lease_expires_at > ?" in sql:
                renew_reached_update.set()
                if not allow_renew_update.wait(timeout=2):
                    raise TimeoutError("late renewal test was not released")
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    def delayed_get_db():
        conn = original_get_db()
        if threading.current_thread().name == "late-rag-renew":
            return DelayedRenewConnection(conn)
        return conn

    monkeypatch.setattr(rag_eval_store, "get_db", delayed_get_db)

    def renew_after_release():
        try:
            renew_result.append(
                rag_eval_store.renew_rag_eval_start_request(*claim)
            )
        except BaseException as exc:
            renew_errors.append(exc)

    renew_thread = threading.Thread(
        target=renew_after_release,
        name="late-rag-renew",
    )
    renew_thread.start()
    try:
        assert renew_reached_update.wait(timeout=2)
        assert rag_eval_store.expire_rag_eval_start_request(*claim) is True
    finally:
        allow_renew_update.set()
        renew_thread.join(timeout=2)

    assert renew_thread.is_alive() is False
    assert renew_errors == []
    assert renew_result == [False]
    mapping = rag_eval_store.get_rag_eval_start_request(
        "late-renew-user",
        "rag-eval:late-renew",
    )
    assert mapping is not None
    assert mapping["claim_token"] != claim_token
    assert mapping["lease_active"] is False
