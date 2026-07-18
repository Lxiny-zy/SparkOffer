"""Background embedding task queue — fire-and-forget with retry, circuit breaker, and error isolation.

Design goals:
- Large knowledge base indexing runs purely in background (no HTTP timeout risk)
- Retry with exponential backoff for transient embedding API failures
- Circuit breaker prevents cascading failures when embedding service is down
- Task deduplication prevents redundant work
- Graceful degradation: failures are logged but never crash the main process
"""
import asyncio
import hashlib
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any

logger = logging.getLogger("uvicorn")


# ── Circuit Breaker ──

class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing, reject calls
    HALF_OPEN = "half_open" # Testing recovery


class EmbeddingCircuitBreaker:
    """Circuit breaker for embedding API calls.

    Prevents cascading failures when the embedding service is unstable.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 2,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0
        # Counters are mutated from the worker loop AND from embedding worker
        # threads (asyncio.to_thread); guard every transition so updates aren't
        # lost and the breaker trips deterministically.
        self._lock = threading.Lock()

    def _maybe_recover_locked(self) -> None:
        """Promote OPEN → HALF_OPEN once the recovery window elapses. Caller holds _lock."""
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                self._success_count = 0
                logger.info("Embedding circuit breaker: OPEN → HALF_OPEN")

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_recover_locked()
            return self._state

    def can_execute(self) -> bool:
        with self._lock:
            self._maybe_recover_locked()
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.HALF_OPEN:
                # Admit at most half_open_max_calls probe calls. Previously
                # _half_open_calls was never incremented, so this throttle never
                # fired and every caller flooded a still-recovering service.
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False
            return False  # OPEN

    def record_success(self):
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.half_open_max_calls:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._half_open_calls = 0
                    logger.info("Embedding circuit breaker: HALF_OPEN → CLOSED (recovered)")
            else:
                self._failure_count = max(0, self._failure_count - 1)

    def record_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._half_open_calls = 0
                logger.warning("Embedding circuit breaker: HALF_OPEN → OPEN (still failing)")
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    f"Embedding circuit breaker: CLOSED → OPEN "
                    f"(failures={self._failure_count}, threshold={self.failure_threshold})"
                )

    def reset(self):
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0


# Global circuit breaker instance — guards the retrieval / memory embedding path
# (vector_memory._embed*). Query-time, latency-sensitive.
_circuit_breaker = EmbeddingCircuitBreaker()

# Dedicated breaker for the background QUEUE (_execute_task gate). Kept SEPARATE
# from the retrieval breaker on purpose: previously both shared one global breaker,
# so a transient retrieval-path failure would trip it and then silently reject
# every queued KB rebuild ("嵌入服务持续不可用") WITHOUT issuing a single embedding
# request — looking exactly like "provider down" when the provider was fine.
# Higher threshold tolerates short 429 bursts from concurrent (num_workers) rebuilds.
_queue_circuit_breaker = EmbeddingCircuitBreaker(failure_threshold=8, recovery_timeout=60.0)


def get_circuit_breaker() -> EmbeddingCircuitBreaker:
    return _circuit_breaker


def get_queue_circuit_breaker() -> EmbeddingCircuitBreaker:
    return _queue_circuit_breaker


# ── Background Task Queue ──

class TaskPriority(Enum):
    HIGH = 1      # Incremental inserts (small, fast)
    NORMAL = 2    # Profile rebuilds
    LOW = 3       # Full index rebuilds


@dataclass(order=True)
class EmbeddingTask:
    priority: int
    task_id: str = field(compare=False)
    func: Callable = field(compare=False)
    args: tuple = field(compare=False, default_factory=tuple)
    kwargs: dict = field(compare=False, default_factory=dict)
    max_retries: int = field(compare=False, default=3)
    retry_count: int = field(compare=False, default=0)
    created_at: float = field(compare=False, default_factory=time.time)
    # Optional metadata for status reporting (UI surfaced)
    user_id: str = field(compare=False, default="")
    topic: str = field(compare=False, default="")
    label: str = field(compare=False, default="")  # human-readable, e.g. "rebuild python"
    file_count: int = field(compare=False, default=0)


@dataclass
class TaskStatus:
    """Per-task status snapshot for UI polling."""
    task_id: str
    user_id: str
    topic: str
    label: str
    state: str  # pending | running | completed | failed
    submitted_at: float
    started_at: float = 0.0
    finished_at: float = 0.0
    file_count: int = 0
    retry_count: int = 0
    error: str = ""
    message: str = ""
    progress_done: int = 0   # embedded nodes so far (full rebuild only)
    progress_total: int = 0  # total nodes to embed; 0 = unknown / not started


class EmbeddingTaskQueue:
    """Async background task queue for embedding operations.

    Features:
    - Priority queue (HIGH > NORMAL > LOW)
    - Task deduplication by task_id
    - Retry with exponential backoff
    - Circuit breaker integration
    - Concurrent worker limit
    """

    def __init__(self, max_workers: int = 2, max_queue_size: int = 100):
        self._queue: asyncio.PriorityQueue = None  # Initialized lazily
        self._active_tasks: set[str] = set()
        self._pending_ids: set[str] = set()
        self._max_workers = max_workers
        self._max_queue_size = max_queue_size
        self._workers: list[asyncio.Task] = []
        # Detached background tasks (delayed retries + auto-start). Tracked so
        # they aren't GC'd mid-flight and can be cancelled on stop().
        self._bg_tasks: set[asyncio.Task] = set()
        self._started = False
        self._stats = {"completed": 0, "failed": 0, "retried": 0, "dropped": 0}
        # task_id -> TaskStatus. Bounded retention via _gc_statuses().
        self._statuses: dict[str, "TaskStatus"] = {}
        self._status_retention = 3600.0  # keep finished tasks 1 hour
        self._last_gc_at = 0.0           # rate-limit GC sweeps

    async def start(self):
        """Start background workers."""
        if self._started:
            return
        self._queue = asyncio.PriorityQueue(maxsize=self._max_queue_size)
        self._started = True
        for i in range(self._max_workers):
            worker = asyncio.create_task(self._worker(f"embed-worker-{i}"))
            self._workers.append(worker)
        logger.info(f"Embedding task queue started with {self._max_workers} workers")

    async def stop(self):
        """Gracefully stop workers."""
        self._started = False
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        for t in list(self._bg_tasks):
            t.cancel()
        self._bg_tasks.clear()

    def submit(
        self,
        task_id: str,
        func: Callable,
        *args,
        priority: TaskPriority = TaskPriority.NORMAL,
        max_retries: int = 3,
        user_id: str = "",
        topic: str = "",
        label: str = "",
        file_count: int = 0,
        **kwargs,
    ) -> bool:
        """Submit a task to the queue. Returns False if deduplicated or queue full.

        Optional metadata (user_id / topic / label / file_count) is surfaced via
        get_status() / list_statuses() so UI can poll progress.
        """
        if task_id in self._pending_ids or task_id in self._active_tasks:
            logger.debug(f"Embedding task deduplicated: {task_id}")
            return False

        if self._started and self._queue.full():
            self._stats["dropped"] += 1
            logger.warning(f"Embedding task queue full, dropping: {task_id}")
            return False

        task = EmbeddingTask(
            priority=priority.value,
            task_id=task_id,
            func=func,
            args=args,
            kwargs=kwargs,
            max_retries=max_retries,
            user_id=user_id,
            topic=topic,
            label=label,
            file_count=file_count,
        )
        # Reserve dedup id + publish a pending status synchronously, even when the
        # loop isn't up yet — otherwise concurrent submits in the startup window
        # would each enqueue a duplicate and get_status() would return None.
        self._pending_ids.add(task_id)
        self._statuses[task_id] = TaskStatus(
            task_id=task_id, user_id=user_id, topic=topic, label=label,
            state="pending", submitted_at=time.time(),
            file_count=file_count, message="队列中，等待执行",
        )
        self._gc_statuses()

        if self._started:
            self._queue.put_nowait(task)
        else:
            # Loop not started yet: start it, then enqueue this exact task.
            self._track(self._auto_start_and_enqueue(task))
        return True

    async def _auto_start_and_enqueue(self, task: "EmbeddingTask"):
        await self.start()
        try:
            self._queue.put_nowait(task)
        except asyncio.QueueFull:
            self._pending_ids.discard(task.task_id)
            self._stats["dropped"] += 1
            logger.warning(f"Embedding task queue full, dropping: {task.task_id}")
            self._update_status(task.task_id, state="failed",
                                finished_at=time.time(), error="队列已满")

    def _track(self, coro) -> asyncio.Task:
        """Run a detached coroutine, keeping a reference so it isn't GC'd mid-flight
        and surfacing any exception in the logs."""
        t = asyncio.create_task(coro)
        self._bg_tasks.add(t)
        t.add_done_callback(self._on_bg_done)
        return t

    def _on_bg_done(self, t: asyncio.Task):
        self._bg_tasks.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc is not None:
                logger.error(f"Embedding queue background task errored: {exc}")

    def _spawn_retry(self, task: EmbeddingTask, delay: float):
        """Reserve the dedup id and schedule a delayed re-enqueue off the worker."""
        self._pending_ids.add(task.task_id)
        self._track(self._schedule_retry(task, delay))

    async def _schedule_retry(self, task: EmbeddingTask, delay: float):
        """Re-enqueue `task` after `delay`s WITHOUT occupying a worker.

        The worker returns as soon as it spawns this, so the backoff sleep no
        longer pins it (which previously starved every other queued task during
        a retry). Uses put_nowait: a full queue drops the retry instead of
        deadlocking — a blocking put run inside a worker can wedge all workers
        once the queue fills and every worker is awaiting put."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self._pending_ids.discard(task.task_id)
            raise
        if not self._started:
            self._pending_ids.discard(task.task_id)
            return
        try:
            self._queue.put_nowait(task)
        except asyncio.QueueFull:
            self._pending_ids.discard(task.task_id)
            self._stats["dropped"] += 1
            logger.warning(f"Embedding task queue full, dropping retry: {task.task_id}")
            self._update_status(task.task_id, state="failed",
                                finished_at=time.time(), error="队列已满，重试已丢弃")

    async def _worker(self, name: str):
        """Worker loop: pull tasks and execute with retry."""
        while self._started:
            try:
                task: EmbeddingTask = await asyncio.wait_for(
                    self._queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            self._pending_ids.discard(task.task_id)
            self._active_tasks.add(task.task_id)
            self._update_status(task.task_id,
                                state="running", started_at=time.time(),
                                message="正在执行")

            try:
                await self._execute_task(task)
            except Exception as e:
                logger.error(f"[{name}] Unhandled error in task {task.task_id}: {e}")
                self._update_status(task.task_id, state="failed",
                                    finished_at=time.time(), error=str(e)[:300])
            finally:
                self._active_tasks.discard(task.task_id)
                self._queue.task_done()
                self._gc_statuses()

    async def _execute_task(self, task: EmbeddingTask):
        """Execute a single task with circuit breaker and retry logic."""
        cb = get_queue_circuit_breaker()

        if not cb.can_execute():
            # Circuit is open — re-enqueue after the recovery timeout WITHOUT
            # sleeping here (that would pin this worker for ~recovery_timeout,
            # starving the rest of the queue). _spawn_retry defers the put.
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                backoff = cb.recovery_timeout + 5.0
                logger.info(
                    f"Circuit breaker OPEN, deferring task {task.task_id} for {backoff:.0f}s"
                )
                self._update_status(task.task_id, state="pending",
                                    message=f"嵌入任务队列熔断中（近期连续失败），{backoff:.0f}s 后重试")
                self._spawn_retry(task, backoff)
            else:
                self._stats["failed"] += 1
                logger.warning(f"Task {task.task_id} exhausted retries (circuit open)")
                self._update_status(task.task_id, state="failed",
                                    finished_at=time.time(),
                                    error="嵌入任务连续失败触发队列熔断，请查看后端日志定位首个失败原因")
            return

        try:
            # Execute the task function
            if asyncio.iscoroutinefunction(task.func):
                await task.func(*task.args, **task.kwargs)
            else:
                await asyncio.to_thread(task.func, *task.args, **task.kwargs)

            cb.record_success()
            self._stats["completed"] += 1
            logger.debug(f"Embedding task completed: {task.task_id}")
            self._update_status(task.task_id, state="completed",
                                finished_at=time.time(), message="完成")

        except Exception as e:
            cb.record_failure()
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                self._stats["retried"] += 1
                # Exponential backoff: 2s, 4s, 8s. Computed from retry_count-1
                # because retry_count was just incremented above (so the first
                # retry waits 2s, not 4s).
                backoff = min(2 ** (task.retry_count - 1) * 2, 30)
                logger.warning(
                    f"Embedding task {task.task_id} failed (attempt {task.retry_count}/{task.max_retries}), "
                    f"retrying in {backoff}s: {e}"
                )
                # Defer the re-enqueue to a detached task so the backoff doesn't
                # occupy this worker, and use put_nowait (never a blocking put).
                self._update_status(
                    task.task_id, retry_count=task.retry_count, state="pending",
                    message=f"失败重试 {task.retry_count}/{task.max_retries}，{backoff}s 后重试",
                )
                self._spawn_retry(task, backoff)
            else:
                self._stats["failed"] += 1
                logger.error(
                    f"Embedding task {task.task_id} permanently failed after "
                    f"{task.max_retries} retries: {e}"
                )
                self._update_status(task.task_id, state="failed",
                                    finished_at=time.time(), error=str(e)[:300])

    def get_stats(self) -> dict:
        return {
            **self._stats,
            "queue_size": self._queue.qsize() if self._queue else 0,
            "active": len(self._active_tasks),
            "circuit_state": _circuit_breaker.state.value,
            "queue_circuit_state": _queue_circuit_breaker.state.value,
        }

    # ── Status tracking API ──

    def _update_status(self, task_id: str, **patch):
        """Apply attribute updates to a TaskStatus entry if present."""
        st = self._statuses.get(task_id)
        if st is None:
            return
        for k, v in patch.items():
            if hasattr(st, k):
                setattr(st, k, v)

    def set_progress(self, task_id: str, done: int, total: int):
        """Report embedding progress for a running task (called from the build thread).

        Surfaced via get_status()/list_statuses() so the UI can render a real
        progress bar instead of just an elapsed timer. Also refreshes `message`
        so even a text-only client shows forward movement.
        """
        st = self._statuses.get(task_id)
        if st is None:
            return
        st.progress_done = done
        st.progress_total = total
        if total > 0:
            st.message = f"嵌入向量 {done}/{total}"

    def _gc_statuses(self, *, force: bool = False):
        """Drop finished statuses older than retention window to bound memory.

        Rate-limited to at most once per minute unless force=True. Called both
        from submit() (new task arriving) and from worker (task completing) so
        long-idle queues don't permanently leak status entries.
        """
        now = time.time()
        if not force and now - self._last_gc_at < 60.0:
            return
        self._last_gc_at = now
        cutoff = now - self._status_retention
        stale = [
            tid for tid, st in self._statuses.items()
            if st.state in ("completed", "failed") and st.finished_at and st.finished_at < cutoff
        ]
        for tid in stale:
            self._statuses.pop(tid, None)

    def get_status(self, task_id: str) -> "TaskStatus | None":
        return self._statuses.get(task_id)

    def list_statuses(self, user_id: str | None = None,
                      task_id_prefix: str | None = None,
                      limit: int = 50) -> list["TaskStatus"]:
        """Return statuses optionally filtered by user / id prefix, newest first."""
        items = list(self._statuses.values())
        if user_id:
            items = [s for s in items if s.user_id == user_id]
        if task_id_prefix:
            items = [s for s in items if s.task_id.startswith(task_id_prefix)]
        items.sort(key=lambda s: s.submitted_at, reverse=True)
        return items[:limit]


# Global task queue singleton
_task_queue = EmbeddingTaskQueue(max_workers=2, max_queue_size=100)


def get_task_queue() -> EmbeddingTaskQueue:
    return _task_queue


# ── Convenience functions for common embedding background tasks ──

def schedule_index_rebuild(topic: str, user_id: str, file_count: int = 0,
                           label: str | None = None) -> str:
    """Schedule a topic index rebuild in background.

    Incremental-first: the builder diffs the file-hash manifest and only
    re-embeds changed files; it falls back to a full re-embed when no manifest /
    index exists (or the caller invalidated it beforehand for a forced rebuild).
    Returns the task_id so callers can poll status. Submitting an in-flight
    rebuild for the same (user, topic) is a no-op (deduplicated).
    """
    task_id = f"rebuild:{user_id}:{topic}"
    _task_queue.submit(
        task_id,
        _do_index_rebuild,
        topic, user_id,
        priority=TaskPriority.LOW,
        max_retries=2,
        user_id=user_id,
        topic=topic,
        label=label or f"重建 {topic} 向量索引",
        file_count=file_count,
    )
    return task_id


def schedule_incremental_insert(topic: str, user_id: str, text: str, source: str = "auto_evolution"):
    """Schedule an incremental index insert in background."""
    # Hash the full text (not hash(text) % 10000): the modulo bucketed distinct
    # fragments into 10k slots, so two different texts for the same (user, topic)
    # could collide and the second was silently dropped as a "duplicate".
    text_key = hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
    task_id = f"insert:{user_id}:{topic}:{source}:{text_key}"
    _task_queue.submit(
        task_id,
        _do_incremental_insert,
        topic, user_id, text, source,
        priority=TaskPriority.HIGH,
        max_retries=3,
    )


def schedule_profile_vector_rebuild(user_id: str):
    """Schedule a profile vector index rebuild in background."""
    task_id = f"profile_rebuild:{user_id}"
    _task_queue.submit(
        task_id,
        _do_profile_vector_rebuild,
        user_id,
        priority=TaskPriority.NORMAL,
        max_retries=2,
    )


def schedule_session_memory_index(
    session_id: str | None,
    topic: str | None,
    summary: str,
    weak_points: list[dict],
    user_id: str,
    strong_points: list[dict] | None = None,
    insight_text: str = "",
):
    """Schedule session memory indexing in background."""
    task_id = f"memory:{user_id}:{session_id or 'none'}:{time.time():.0f}"
    _task_queue.submit(
        task_id,
        _do_session_memory_index,
        session_id, topic, summary, weak_points, user_id, strong_points, insight_text,
        priority=TaskPriority.HIGH,
        max_retries=3,
    )


# ── Task implementations ──

def _do_index_rebuild(topic: str, user_id: str):
    """Synchronous full index rebuild (runs in thread pool)."""
    from backend.indexer import build_topic_index
    # Report embedding progress back to this task's status so the UI shows a real
    # progress bar. task_id is deterministic (see schedule_index_rebuild).
    task_id = f"rebuild:{user_id}:{topic}"

    def _progress(done: int, total: int):
        _task_queue.set_progress(task_id, done, total)

    build_topic_index(topic, user_id, force_rebuild=True, progress_cb=_progress)
    logger.info(f"Background index rebuild completed: topic={topic}, user={user_id}")


def _do_incremental_insert(topic: str, user_id: str, text: str, source: str = "auto_evolution"):
    """Synchronous incremental insert (runs in thread pool)."""
    from backend.indexer import incremental_insert_to_index
    incremental_insert_to_index(topic, user_id, text, source=source)


def _do_profile_vector_rebuild(user_id: str):
    """Synchronous profile vector rebuild (runs in thread pool)."""
    from backend.vector_memory import rebuild_index_from_profile
    rebuild_index_from_profile(user_id)


async def _do_session_memory_index(
    session_id, topic, summary, weak_points, user_id, strong_points, insight_text,
):
    """Async session memory indexing."""
    from backend.vector_memory import index_session_memory
    await index_session_memory(
        session_id=session_id,
        topic=topic,
        summary=summary,
        weak_points=weak_points,
        user_id=user_id,
        strong_points=strong_points,
        insight_text=insight_text,
    )
