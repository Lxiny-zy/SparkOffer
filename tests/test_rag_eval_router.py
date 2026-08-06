"""Request-boundary tests for starting RAG evaluation jobs."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi import HTTPException

import backend.eval.rag_benchmark as benchmark
import backend.routers.rag_eval as router
import backend.storage.database as database
from backend.storage import rag_eval_store


@pytest.fixture
def isolated_db(tmp_path):
    original_path = database.DB_PATH
    original_conn = getattr(database._local, "conn", None)
    database.DB_PATH = tmp_path / "rag-eval-router.db"
    database._local.conn = None
    database.init_all_tables()
    try:
        yield
    finally:
        temp_conn = getattr(database._local, "conn", None)
        if temp_conn is not None:
            temp_conn.close()
        database.DB_PATH = original_path
        database._local.conn = original_conn


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
        "user-duplicate", "python", "topic", "frozen_retrieval", "production_replay",
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


def test_durable_start_replays_terminal_job_after_inflight_cleanup(
    isolated_db, monkeypatch,
):
    monkeypatch.setattr(router, "load_topics", lambda user_id: {"python": {}})
    monkeypatch.setattr(
        benchmark,
        "load_frozen_cases",
        lambda topic, limit: ([object()], "2", "dataset-hash"),
    )
    calls = 0

    async def fake_run(job, *args, **kwargs):
        nonlocal calls
        calls += 1
        job["run_id"] = await asyncio.to_thread(
            rag_eval_store.save_failed_rag_eval_run,
            job_id=job["job_id"],
            user_id="durable-user",
            topic="python",
            scope="topic",
            n_questions=1,
            k=8,
            judge_mode="none",
            eval_kind="frozen_retrieval",
            retrieval_mode="production_replay",
            seed=42,
            error="terminal test record",
            durable_claim=job["_durable_claim"],
        )
        job["status"] = "completed"

    monkeypatch.setattr(benchmark, "run_frozen_benchmark", fake_run)
    request = router.RagEvalStartRequest(
        topic="python",
        eval_kind="frozen_retrieval",
        retrieval_mode="production_replay",
    )

    async def start_and_finish():
        response = await router.start_rag_eval(
            request, "durable-user", "rag-eval:lost-response",
        )
        await asyncio.gather(*tuple(router._eval_tasks))
        return response

    first = asyncio.run(start_and_finish())
    router.rag_eval_jobs.pop(first["job_id"], None)
    # Dataset coverage can disappear after the first call. A durable replay must
    # happen before loading the mutable dataset and still return the old job.
    def dataset_should_not_be_loaded(topic, limit):
        raise AssertionError("terminal idempotent replay reloaded the dataset")

    monkeypatch.setattr(benchmark, "load_frozen_cases", dataset_should_not_be_loaded)

    second = asyncio.run(router.start_rag_eval(
        request, "durable-user", "rag-eval:lost-response",
    ))

    assert second["job_id"] == first["job_id"]
    assert second["reused"] is True
    assert second["n_questions"] == first["n_questions"] == 1
    assert second["judge_mode"] == first["judge_mode"] == "none"
    assert calls == 1


def test_durable_start_rejects_key_reuse_for_different_request(
    isolated_db, monkeypatch,
):
    monkeypatch.setattr(
        router,
        "load_topics",
        lambda user_id: {"python": {}, "java": {}},
    )
    monkeypatch.setattr(
        benchmark,
        "load_frozen_cases",
        lambda topic, limit: ([object()], "2", "dataset-hash"),
    )

    async def fake_run(job, *args, **kwargs):
        job["status"] = "completed"

    monkeypatch.setattr(benchmark, "run_frozen_benchmark", fake_run)

    async def exercise():
        first = router.RagEvalStartRequest(
            topic="python",
            eval_kind="frozen_retrieval",
            retrieval_mode="production_replay",
        )
        await router.start_rag_eval(first, "conflict-user", "rag-eval:same-key")
        await asyncio.gather(*tuple(router._eval_tasks))
        second = router.RagEvalStartRequest(
            topic="java",
            eval_kind="frozen_retrieval",
            retrieval_mode="production_replay",
        )
        with pytest.raises(HTTPException) as exc_info:
            await router.start_rag_eval(
                second, "conflict-user", "rag-eval:same-key",
            )
        return exc_info.value

    error = asyncio.run(exercise())
    assert error.status_code == 409


def test_expired_ghost_mapping_rebuilds_original_job_id(
    isolated_db, monkeypatch,
):
    monkeypatch.setattr(router, "load_topics", lambda user_id: {"python": {}})
    monkeypatch.setattr(
        benchmark,
        "load_frozen_cases",
        lambda topic, limit: ([object()], "2", "dataset-hash"),
    )
    calls = 0

    async def fake_run(job, *args, **kwargs):
        nonlocal calls
        calls += 1
        job["status"] = "completed"

    monkeypatch.setattr(benchmark, "run_frozen_benchmark", fake_run)
    request = router.RagEvalStartRequest(
        topic="python",
        eval_kind="frozen_retrieval",
        retrieval_mode="production_replay",
    )
    normalized = router._normalized_start_request(request, 20, 8, "none")
    request_hash = router._start_request_hash(normalized)
    state, job_id, stale_token = rag_eval_store.claim_rag_eval_start_request(
        "ghost-user",
        "rag-eval:ghost",
        request_hash,
        "ghost-job",
        lease_for=timedelta(seconds=-1),
    )
    assert state == "claimed"
    assert stale_token is not None
    stale_claim = (
        "ghost-user",
        "rag-eval:ghost",
        request_hash,
        job_id,
        stale_token,
    )
    router.rag_eval_jobs[job_id] = {
        "job_id": job_id,
        "user_id": "ghost-user",
        "topic": "stale-topic",
        "n_questions": 99,
        "status": "running",
        "_durable_claim": stale_claim,
    }

    async def rebuild():
        response = await router.start_rag_eval(
            request, "ghost-user", "rag-eval:ghost",
        )
        await asyncio.gather(*tuple(router._eval_tasks))
        return response

    try:
        response = asyncio.run(rebuild())
        rebuilt_claim = router.rag_eval_jobs[job_id]["_durable_claim"]
    finally:
        router.rag_eval_jobs.pop(job_id, None)
    assert response["job_id"] == job_id == "ghost-job"
    assert response["reused"] is True
    assert rebuilt_claim[4] != stale_token
    assert calls == 1


def test_status_keeps_polling_for_job_leased_by_another_worker(isolated_db):
    state, job_id, _ = rag_eval_store.claim_rag_eval_start_request(
        "remote-user",
        "rag-eval:remote",
        "request-hash",
        "remote-job",
        response={
            "topic": "python",
            "n_questions": 7,
            "judge_mode": "none",
            "eval_kind": "frozen_retrieval",
            "retrieval_mode": "production_replay",
            "seed": 42,
            "estimated_llm_calls": 0,
        },
    )
    assert state == "claimed"
    router.rag_eval_jobs.pop(job_id, None)

    status = asyncio.run(router.rag_eval_status(job_id, "remote-user"))

    assert status["job_id"] == job_id
    assert status["status"] == "pending"
    assert status["topic"] == "python"
    assert status["n_questions"] == 7


def test_status_discards_live_job_after_claim_is_replaced(isolated_db):
    state, job_id, stale_token = rag_eval_store.claim_rag_eval_start_request(
        "status-stale-user",
        "rag-eval:status-stale",
        "status-hash",
        "status-stale-job",
        lease_for=timedelta(seconds=-1),
        response={
            "topic": "python",
            "n_questions": 2,
            "judge_mode": "none",
            "eval_kind": "frozen_retrieval",
            "retrieval_mode": "production_replay",
            "seed": 42,
            "estimated_llm_calls": 0,
        },
    )
    assert state == "claimed"
    assert stale_token is not None
    current_token = rag_eval_store.reclaim_rag_eval_start_request(
        "status-stale-user",
        "rag-eval:status-stale",
        "status-hash",
        job_id,
    )
    assert current_token is not None
    router.rag_eval_jobs[job_id] = {
        "job_id": job_id,
        "user_id": "status-stale-user",
        "topic": "python",
        "n_questions": 2,
        "status": "running",
        "_durable_claim": (
            "status-stale-user",
            "rag-eval:status-stale",
            "status-hash",
            job_id,
            stale_token,
        ),
    }

    try:
        status = asyncio.run(
            router.rag_eval_status(job_id, "status-stale-user")
        )
        stale_job_was_discarded = job_id not in router.rag_eval_jobs
    finally:
        router.rag_eval_jobs.pop(job_id, None)

    assert status["status"] == "pending"
    assert status["job_id"] == job_id
    assert stale_job_was_discarded is True


def test_durable_replay_ignores_local_job_with_stale_claim_token(monkeypatch):
    job_id = "reclaimed-job"
    router.rag_eval_jobs[job_id] = {
        "job_id": job_id,
        "user_id": "reclaimed-user",
        "topic": "stale-topic",
        "n_questions": 99,
        "status": "running",
        "_durable_claim": (
            "reclaimed-user",
            "rag-eval:key",
            "request-hash",
            job_id,
            "stale-token",
        ),
    }
    monkeypatch.setattr(
        rag_eval_store,
        "get_rag_eval_run_by_job",
        lambda *args, **kwargs: None,
    )
    mapping = {
        "job_id": job_id,
        "request_hash": "request-hash",
        "claim_token": "winning-token",
        "lease_active": True,
        "response": {
            "topic": "winning-topic",
            "scope": "topic",
            "n_questions": 3,
            "k": 8,
            "judge_mode": "none",
            "eval_kind": "frozen_retrieval",
            "retrieval_mode": "production_replay",
            "seed": 42,
            "estimated_llm_calls": 0,
        },
    }
    fallback = {
        "topic": "fallback-topic",
        "scope": "topic",
        "n_questions": 1,
        "k": 8,
        "judge_mode": "none",
        "eval_kind": "frozen_retrieval",
        "retrieval_mode": "production_replay",
        "seed": 42,
    }
    try:
        response = asyncio.run(router._resolved_durable_start_response(
            mapping,
            "reclaimed-user",
            fallback,
            0,
        ))
    finally:
        router.rag_eval_jobs.pop(job_id, None)

    assert response is not None
    assert response["topic"] == "winning-topic"
    assert response["n_questions"] == 3


def test_start_lease_heartbeat_retries_after_sqlite_error(monkeypatch):
    calls = 0

    def flaky_renew(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary sqlite failure")
        return False

    monkeypatch.setattr(
        rag_eval_store,
        "renew_rag_eval_start_request",
        flaky_renew,
    )
    monkeypatch.setattr(router, "_START_LEASE_HEARTBEAT_SECONDS", 0)

    with pytest.raises(rag_eval_store.RagEvalLeaseLostError):
        asyncio.run(router._renew_start_lease((
            "user",
            "rag-eval:key",
            "request-hash",
            "job-id",
            "claim-token",
        )))

    assert calls == 2


def test_lost_start_lease_cancels_benchmark_and_discards_local_job(monkeypatch):
    claim = (
        "lease-user",
        "rag-eval:lease-key",
        "request-hash",
        "leased-job",
        "claim-token",
    )
    cancelled = False
    router.rag_eval_jobs[claim[3]] = {
        "job_id": claim[3],
        "user_id": claim[0],
        "status": "running",
        "phase": "evaluating",
        "_durable_claim": claim,
    }

    monkeypatch.setattr(
        rag_eval_store,
        "renew_rag_eval_start_request",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        rag_eval_store,
        "expire_rag_eval_start_request",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(router, "_START_LEASE_HEARTBEAT_SECONDS", 0)

    async def benchmark_until_cancelled():
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    async def exercise():
        with pytest.raises(rag_eval_store.RagEvalLeaseLostError):
            await router._run_with_global_slot(
                benchmark_until_cancelled(),
                claim,
            )

    try:
        asyncio.run(exercise())
    finally:
        router.rag_eval_jobs.pop(claim[3], None)

    assert cancelled is True
    assert claim[3] not in router.rag_eval_jobs


def test_external_task_cancellation_discards_durable_live_job(monkeypatch):
    claim = (
        "cancel-user",
        "rag-eval:cancel-key",
        "request-hash",
        "cancelled-job",
        "claim-token",
    )
    router.rag_eval_jobs[claim[3]] = {
        "job_id": claim[3],
        "user_id": claim[0],
        "status": "running",
        "phase": "evaluating",
        "_durable_claim": claim,
    }
    monkeypatch.setattr(
        rag_eval_store,
        "expire_rag_eval_start_request",
        lambda *args, **kwargs: True,
    )

    async def exercise():
        started = asyncio.Event()

        async def running_benchmark():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(router._run_with_global_slot(
            running_benchmark(),
            claim,
        ))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(exercise())
    finally:
        router.rag_eval_jobs.pop(claim[3], None)

    assert claim[3] not in router.rag_eval_jobs


def test_start_lease_heartbeat_fails_before_exception_budget_expires(monkeypatch):
    calls = 0
    now = 0.0

    def unavailable_sqlite(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("sqlite remains unavailable")

    monkeypatch.setattr(
        rag_eval_store,
        "renew_rag_eval_start_request",
        unavailable_sqlite,
    )
    monkeypatch.setattr(router, "_START_LEASE_HEARTBEAT_SECONDS", 0.005)
    monkeypatch.setattr(router, "_START_LEASE_DURATION_SECONDS", 0.04)
    monkeypatch.setattr(router, "_START_LEASE_RENEWAL_GUARD_SECONDS", 0.015)
    monkeypatch.setattr(router, "_START_LEASE_MIN_RETRY_SECONDS", 0.001)

    def fake_time():
        return now

    async def advance_time(delay):
        nonlocal now
        now += delay

    monkeypatch.setattr(router, "_start_lease_time", fake_time)
    monkeypatch.setattr(router, "_start_lease_sleep", advance_time)

    with pytest.raises(rag_eval_store.RagEvalLeaseLostError):
        asyncio.run(router._renew_start_lease((
            "user",
            "rag-eval:key",
            "request-hash",
            "job-id",
            "claim-token",
        )))

    assert calls >= 2
