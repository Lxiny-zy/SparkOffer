"""SQLite compatibility tests for persisted RAG benchmark runs."""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

import backend.storage.database as database
from backend.live_store import rag_eval_jobs
from backend.routers.rag_eval import rag_eval_status
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
    } <= columns


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
