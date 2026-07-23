from __future__ import annotations

import asyncio
import threading

import pytest

import backend.embedding_tasks as embedding_tasks
import backend.indexer as indexer
import backend.routers.knowledge as knowledge
from backend.embedding_tasks import (
    CircuitState,
    EmbeddingCircuitBreaker,
    EmbeddingTaskQueue,
    TaskSubmitResult,
)


async def _wait_until(predicate, timeout: float = 2.0):
    async def _poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


def test_force_rebuild_does_not_invalidate_until_task_is_queued(monkeypatch):
    calls = {}
    evicted = []

    def fake_schedule(topic, user_id, file_count=0, label=None, force_invalidate=False):
        calls.update({
            "topic": topic,
            "user_id": user_id,
            "file_count": file_count,
            "label": label,
            "force_invalidate": force_invalidate,
        })
        return TaskSubmitResult(
            task_id=f"rebuild:{user_id}:{topic}",
            submitted=False,
            reason="queue_full",
        )

    monkeypatch.setattr(knowledge, "_count_files", lambda user_id, dir_name: 7)
    monkeypatch.setattr(knowledge, "try_schedule_index_rebuild", fake_schedule)
    monkeypatch.setattr(knowledge, "evict_topic_cache", lambda *args: evicted.append(args))

    manifest = asyncio.run(
        knowledge._submit_rebuild(
            "python", {"dir": "python", "name": "Python"}, "user-1", force=True,
        )
    )

    assert manifest == {
        "task_id": "rebuild:user-1:python",
        "topic": "python",
        "file_count": 7,
        "submitted": False,
        "reason": "queue_full",
    }
    assert calls["force_invalidate"] is True
    assert evicted == []


def test_followup_submitted_before_queue_start_is_not_lost():
    async def scenario():
        queue = EmbeddingTaskQueue(max_workers=1, max_queue_size=2)
        calls = []

        async def record(value, force_invalidate=False):
            calls.append((value, force_invalidate))

        assert queue.submit(
            "same", record, "first", max_retries=0, coalesce=True,
        )
        assert queue.submit(
            "same", record, "latest", max_retries=0, coalesce=True,
            force_invalidate=True,
        )

        await _wait_until(
            lambda: len(calls) == 2
            and queue.get_status("same").state == "completed"
        )
        assert calls == [("first", False), ("latest", True)]
        await queue.stop()

    asyncio.run(scenario())


def test_prestart_capacity_rejection_is_synchronous():
    async def scenario():
        queue = EmbeddingTaskQueue(max_workers=1, max_queue_size=1)
        gate = asyncio.Event()

        async def blocked():
            await gate.wait()

        assert queue.submit("first", blocked, max_retries=0)
        assert not queue.submit("overflow", blocked, max_retries=0)
        assert queue.get_status("overflow") is None

        gate.set()
        await _wait_until(
            lambda: queue.get_status("first").state == "completed"
        )
        await queue.stop()

    asyncio.run(scenario())


def test_latest_followup_runs_after_active_task_and_force_is_sticky():
    async def scenario():
        queue = EmbeddingTaskQueue(max_workers=1, max_queue_size=2)
        gate = asyncio.Event()
        calls = []

        async def record(value, force_invalidate=False):
            calls.append((value, force_invalidate))
            if value == "first":
                await gate.wait()

        await queue.start()
        assert queue.submit(
            "same", record, "first", max_retries=0, coalesce=True,
        )
        await _wait_until(lambda: queue.get_status("same").state == "running")
        assert queue.submit(
            "same", record, "forced", max_retries=0, coalesce=True,
            force_invalidate=True,
        )
        assert queue.submit(
            "same", record, "latest", max_retries=0, coalesce=True,
            force_invalidate=False,
        )

        gate.set()
        await _wait_until(
            lambda: len(calls) == 2
            and queue.get_status("same").state == "completed"
        )
        assert calls == [("first", False), ("latest", True)]
        await queue.stop()

    asyncio.run(scenario())


def test_generic_coalesced_task_does_not_receive_rebuild_only_keyword():
    async def scenario():
        queue = EmbeddingTaskQueue(max_workers=1, max_queue_size=2)
        gate = asyncio.Event()
        calls = []

        async def record(value):
            calls.append(value)
            if value == "first":
                await gate.wait()

        await queue.start()
        assert queue.submit("same", record, "first", max_retries=0, coalesce=True)
        await _wait_until(lambda: queue.get_status("same").state == "running")
        assert queue.submit("same", record, "middle", max_retries=0, coalesce=True)
        assert queue.submit("same", record, "latest", max_retries=0, coalesce=True)

        gate.set()
        await _wait_until(
            lambda: calls == ["first", "latest"]
            and queue.get_status("same").state == "completed"
        )
        await queue.stop()

    asyncio.run(scenario())


def test_failed_attempt_promotes_followup():
    async def scenario():
        queue = EmbeddingTaskQueue(max_workers=1, max_queue_size=2)
        gate = asyncio.Event()
        calls = []

        async def run(value):
            calls.append(value)
            if value == "first":
                await gate.wait()
                raise RuntimeError("expected failure")

        await queue.start()
        assert queue.submit("same", run, "first", max_retries=0, coalesce=True)
        await _wait_until(lambda: queue.get_status("same").state == "running")
        assert queue.submit("same", run, "followup", max_retries=0, coalesce=True)
        gate.set()

        await _wait_until(
            lambda: calls == ["first", "followup"]
            and queue.get_status("same").state == "completed"
        )
        await queue.stop()

    asyncio.run(scenario())


def test_stop_finalizes_pending_work_and_allows_same_id_after_restart():
    async def scenario():
        queue = EmbeddingTaskQueue(max_workers=1, max_queue_size=2)
        gate = asyncio.Event()
        calls = []

        async def blocked(value):
            calls.append(value)
            await gate.wait()

        await queue.start()
        assert queue.submit("same", blocked, "first", max_retries=0, coalesce=True)
        await _wait_until(lambda: queue.get_status("same").state == "running")
        assert queue.submit("same", blocked, "followup", max_retries=0, coalesce=True)

        await queue.stop()
        status = queue.get_status("same")
        assert status.state == "failed"
        assert "stopped" in status.error.lower()
        assert not queue._pending_ids
        assert not queue._active_tasks
        assert not queue._followup_tasks

        async def complete(value):
            calls.append(value)

        await queue.start()
        assert queue.submit("same", complete, "restart", max_retries=0)
        await _wait_until(lambda: queue.get_status("same").state == "completed")
        assert calls == ["first", "restart"]
        await queue.stop()

    asyncio.run(scenario())


def test_stop_waits_for_underlying_sync_thread_before_restart():
    async def scenario():
        queue = EmbeddingTaskQueue(max_workers=1, max_queue_size=2)
        started = threading.Event()
        release = threading.Event()
        calls = []

        def blocked(value):
            calls.append(value)
            started.set()
            release.wait(timeout=2)

        await queue.start()
        assert queue.submit("same", blocked, "first", max_retries=0)
        await _wait_until(started.is_set)

        stop_task = asyncio.create_task(queue.stop())
        await asyncio.sleep(0.05)
        try:
            assert not stop_task.done()
            assert not queue.submit("during-stop", calls.append, "rejected")
            with pytest.raises(RuntimeError, match="still stopping"):
                await queue.start()
        finally:
            release.set()
            await asyncio.wait_for(stop_task, timeout=2)

        await queue.start()
        assert queue.submit("same", calls.append, "restart", max_retries=0)
        await _wait_until(lambda: queue.get_status("same").state == "completed")
        assert calls == ["first", "restart"]
        await queue.stop()

    asyncio.run(scenario())


def test_rejected_compatibility_rebuild_marks_persisted_index_dirty(
    monkeypatch,
):
    class RejectingQueue:
        is_stopping = False

        @staticmethod
        def get_status(_task_id):
            return None

        @staticmethod
        def submit(*_args, **_kwargs):
            return False

    marked = []
    monkeypatch.setattr(embedding_tasks, "_task_queue", RejectingQueue())
    monkeypatch.setattr(
        indexer,
        "mark_topic_index_dirty",
        lambda topic, user_id: marked.append((topic, user_id)),
    )

    task_id = embedding_tasks.schedule_index_rebuild("python", "user-1")

    assert task_id == "rebuild:user-1:python"
    assert marked == [("python", "user-1")]


def test_rejected_incremental_insert_marks_source_index_dirty(monkeypatch):
    class RejectingQueue:
        @staticmethod
        def submit(*_args, **_kwargs):
            return False

    marked = []
    monkeypatch.setattr(embedding_tasks, "_task_queue", RejectingQueue())
    monkeypatch.setattr(
        indexer,
        "mark_topic_index_dirty",
        lambda topic, user_id: marked.append((topic, user_id)),
    )

    assert not embedding_tasks.schedule_incremental_insert(
        "python", "user-1", "new source content",
    )
    assert marked == [("python", "user-1")]


def test_cancelled_half_open_probe_releases_admission():
    breaker = EmbeddingCircuitBreaker(
        failure_threshold=1,
        recovery_timeout=0,
        half_open_max_calls=1,
    )
    breaker.record_failure()

    assert breaker.state == CircuitState.HALF_OPEN
    permit = breaker.acquire()
    assert permit is not None
    assert breaker.acquire() is None

    breaker.release_probe(permit)
    replacement = breaker.acquire()
    assert replacement is not None
    breaker.record_success(replacement)
    assert breaker.state == CircuitState.CLOSED


def test_old_closed_request_cannot_release_or_complete_new_half_open_probe():
    breaker = EmbeddingCircuitBreaker(
        failure_threshold=1,
        recovery_timeout=0,
        half_open_max_calls=1,
    )
    old_closed_permit = breaker.acquire()
    assert old_closed_permit is not None

    breaker.record_failure()
    assert breaker.state == CircuitState.HALF_OPEN
    recovery_probe = breaker.acquire()
    assert recovery_probe is not None

    breaker.release_probe(old_closed_permit)
    breaker.record_success(old_closed_permit)
    assert breaker.state == CircuitState.HALF_OPEN
    assert breaker.acquire() is None

    breaker.record_success(recovery_probe)
    assert breaker.state == CircuitState.CLOSED


def test_old_closed_request_cannot_fail_new_half_open_probe():
    breaker = EmbeddingCircuitBreaker(
        failure_threshold=1,
        recovery_timeout=0,
        half_open_max_calls=1,
    )
    stale_closed = breaker.acquire()
    opener = breaker.acquire()
    assert stale_closed is not None
    assert opener is not None

    breaker.record_failure(opener)
    assert breaker.state == CircuitState.HALF_OPEN
    current_probe = breaker.acquire()
    assert current_probe is not None

    breaker.record_failure(stale_closed)
    assert breaker.state == CircuitState.HALF_OPEN

    breaker.record_success(current_probe)
    assert breaker.state == CircuitState.CLOSED


def test_tokenless_half_open_completion_cannot_mutate_recovery_probe():
    breaker = EmbeddingCircuitBreaker(
        failure_threshold=1,
        recovery_timeout=0,
        half_open_max_calls=1,
    )
    breaker.record_failure()

    assert breaker.state == CircuitState.HALF_OPEN
    permit = breaker.acquire()
    assert permit is not None

    # Legacy/unadmitted callbacks must not consume, complete, or release the
    # current probe admission.
    breaker.record_failure()
    breaker.record_success()
    breaker.release_probe()
    assert breaker.state == CircuitState.HALF_OPEN
    assert breaker.acquire() is None

    breaker.record_success(permit)
    assert breaker.state == CircuitState.CLOSED


def test_async_rebuild_does_not_report_failure_without_circuit_permit(monkeypatch):
    calls = []

    def fail_build(*_args, **_kwargs):
        raise RuntimeError("build failed")

    class Breaker:
        def record_failure(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(indexer, "build_topic_index", fail_build)
    monkeypatch.setattr(
        "backend.embedding_tasks.get_circuit_breaker", lambda: Breaker(),
    )

    asyncio.run(indexer.async_rebuild_topic_index("python", "user-1"))

    assert calls == []
