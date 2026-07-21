"""Request-boundary tests for starting RAG evaluation jobs."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import backend.eval.rag_benchmark as benchmark
import backend.routers.rag_eval as router


def test_frozen_start_rejects_topic_without_fixed_cases(monkeypatch):
    monkeypatch.setattr(router, "load_topics", lambda user_id: {"custom": {}})
    monkeypatch.setattr(
        benchmark,
        "load_frozen_cases",
        lambda topic, limit: ([], "2", "dataset-hash"),
    )
    request = router.RagEvalStartRequest(
        topic="custom", eval_kind="frozen_retrieval",
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(router.start_rag_eval(request, "user-1"))

    assert exc_info.value.status_code == 422


def test_frozen_start_reports_selected_count_and_forces_no_judge(monkeypatch):
    monkeypatch.setattr(router, "load_topics", lambda user_id: {"python": {}})
    monkeypatch.setattr(
        benchmark,
        "load_frozen_cases",
        lambda topic, limit: ([object(), object()], "2", "dataset-hash"),
    )

    async def fake_run(job, *args, **kwargs):
        job["status"] = "completed"

    monkeypatch.setattr(benchmark, "run_frozen_benchmark", fake_run)
    request = router.RagEvalStartRequest(
        topic="python",
        n_questions=20,
        judge_mode="full",
        eval_kind="frozen_retrieval",
    )

    async def run_start():
        response = await router.start_rag_eval(request, "user-2")
        await asyncio.sleep(0)
        return response

    response = asyncio.run(run_start())

    assert response["n_questions"] == 2
    assert response["judge_mode"] == "none"


def test_duplicate_inflight_start_returns_existing_job(monkeypatch):
    monkeypatch.setattr(router, "load_topics", lambda user_id: {"python": {}})
    monkeypatch.setattr(
        benchmark,
        "load_frozen_cases",
        lambda topic, limit: ([object()], "2", "dataset-hash"),
    )
    key = "|".join((
        "user-duplicate", "python", "frozen_retrieval", "production_replay",
        "1", "8", "none", "42",
    ))
    job_id = "existing-job"
    router.rag_eval_jobs[job_id] = {
        "job_id": job_id,
        "user_id": "user-duplicate",
        "topic": "python",
        "n_questions": 1,
        "judge_mode": "none",
        "eval_kind": "frozen_retrieval",
        "retrieval_mode": "production_replay",
        "seed": 42,
        "status": "running",
    }
    router._inflight[key] = job_id
    request = router.RagEvalStartRequest(
        topic="python", eval_kind="frozen_retrieval",
        retrieval_mode="production_replay",
    )
    try:
        response = asyncio.run(router.start_rag_eval(request, "user-duplicate"))
    finally:
        router.rag_eval_jobs.pop(job_id, None)
        router._inflight.pop(key, None)

    assert response["job_id"] == job_id
    assert response["reused"] is True
