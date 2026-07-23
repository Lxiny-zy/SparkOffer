"""RAG 评测路由 —— 按需触发的离线 RAGAS 基准评测（异步任务 + 进度轮询）。

刻意不走 embedding 任务队列（仅 2 worker、在索引写入关键路径上）：分钟级评测会
饿死嵌入写入。改用独立 asyncio.create_task，进度写入 live_store.rag_eval_jobs，
前端轮询 /status。结果落 rag_eval_runs 表。
"""
import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Depends, Header, Query
from pydantic import BaseModel, Field
from typing import Annotated, Literal

from backend.indexer import load_topics
from backend.auth import get_current_user
from backend.live_store import rag_eval_jobs
from backend.rag_eval import run_eval
from backend.storage import rag_eval_store

router = APIRouter(prefix="/api")
logger = logging.getLogger("uvicorn")

MAX_EVAL_LLM_CALLS = 300
MAX_QUEUED_EVAL_JOBS = 4

# 强引用在途任务，防止 asyncio 只持弱引用导致任务被 GC（同 main.warmup_task /
# embedding_tasks._bg_tasks 的做法）。
_eval_tasks: set[asyncio.Task] = set()
# One benchmark at a time per backend process. Each run already fans out LLM and
# embedding calls internally; allowing different topics to multiply those
# semaphores defeats their provider-rate-limit protection.
_eval_job_semaphore = asyncio.Semaphore(1)
# Exact request fingerprint -> job_id, used for idempotent in-flight retries.
_inflight: dict[str, str] = {}
_START_LEASE_HEARTBEAT_SECONDS = 30
_START_LEASE_DURATION_SECONDS = 120
_START_LEASE_RENEWAL_GUARD_SECONDS = 30
_START_LEASE_MIN_RETRY_SECONDS = 0.01
_BENCHMARK_CANCEL_WAIT_SECONDS = 5


async def _renew_start_lease(claim: tuple[str, str, str, str, str]) -> None:
    user_id, idempotency_key, request_hash, job_id, claim_token = claim
    loop = asyncio.get_running_loop()
    confirmed_until = loop.time() + _START_LEASE_DURATION_SECONDS
    while True:
        remaining = confirmed_until - loop.time()
        if remaining <= _START_LEASE_RENEWAL_GUARD_SECONDS:
            raise rag_eval_store.RagEvalLeaseLostError(
                f"Could not confirm RAG eval lease before expiry for {job_id}"
            )
        retry_in = min(
            max(_START_LEASE_MIN_RETRY_SECONDS, _START_LEASE_HEARTBEAT_SECONDS),
            max(_START_LEASE_MIN_RETRY_SECONDS, remaining - _START_LEASE_RENEWAL_GUARD_SECONDS),
        )
        await asyncio.sleep(retry_in)
        try:
            renewed = await asyncio.to_thread(
                rag_eval_store.renew_rag_eval_start_request,
                user_id,
                idempotency_key,
                request_hash,
                job_id,
                claim_token,
                lease_for=timedelta(seconds=_START_LEASE_DURATION_SECONDS),
            )
        except Exception as exc:
            logger.warning(
                "Could not renew RAG eval start lease for %s: %s",
                job_id,
                exc,
            )
            if (
                loop.time()
                >= confirmed_until - _START_LEASE_RENEWAL_GUARD_SECONDS
            ):
                raise rag_eval_store.RagEvalLeaseLostError(
                    f"Could not renew RAG eval lease before expiry for {job_id}"
                ) from exc
            continue
        if not renewed:
            raise rag_eval_store.RagEvalLeaseLostError(
                f"RAG eval lease was lost for {job_id}"
            )
        confirmed_until = loop.time() + _START_LEASE_DURATION_SECONDS


def _discard_stale_live_job(
    claim: tuple[str, str, str, str, str],
    reason: str,
) -> None:
    job_id = claim[3]
    job = rag_eval_jobs[job_id] if job_id in rag_eval_jobs else None
    if job is None or job.get("_durable_claim") != claim:
        return
    job.update({
        "status": "failed",
        "phase": "failed",
        "error": reason[:300],
        "updated_at": time.time(),
    })
    rag_eval_jobs.pop(job_id, None)


async def _raise_heartbeat_failure(heartbeat: asyncio.Task, job_id: str) -> None:
    try:
        await heartbeat
    except rag_eval_store.RagEvalPersistenceFenceError:
        raise
    except asyncio.CancelledError as exc:
        raise rag_eval_store.RagEvalLeaseLostError(
            f"RAG eval lease heartbeat was cancelled for {job_id}"
        ) from exc
    except Exception as exc:
        raise rag_eval_store.RagEvalLeaseLostError(
            f"RAG eval lease heartbeat failed for {job_id}"
        ) from exc
    raise rag_eval_store.RagEvalLeaseLostError(
        f"RAG eval lease heartbeat stopped for {job_id}"
    )


def _log_late_benchmark_outcome(task: asyncio.Task, job_id: str) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Cancelled stale RAG eval benchmark %s later failed",
            job_id,
            exc_info=(type(exc), exc, exc.__traceback__),
        )


async def _cancel_benchmark(benchmark: asyncio.Task, job_id: str) -> None:
    if benchmark.done():
        _log_late_benchmark_outcome(benchmark, job_id)
        return
    benchmark.cancel()
    done, _ = await asyncio.wait(
        {benchmark},
        timeout=_BENCHMARK_CANCEL_WAIT_SECONDS,
    )
    if benchmark not in done:
        logger.error(
            "Stale RAG eval benchmark %s did not stop after cancellation",
            job_id,
        )
        benchmark.add_done_callback(
            lambda task, current_job_id=job_id: _log_late_benchmark_outcome(
                task,
                current_job_id,
            )
        )
        return
    _log_late_benchmark_outcome(benchmark, job_id)


async def _run_with_global_slot(
    awaitable,
    durable_claim: tuple[str, str, str, str, str] | None = None,
) -> None:
    heartbeat: asyncio.Task | None = None
    slot_waiter: asyncio.Task | None = None
    benchmark: asyncio.Task | None = None
    slot_acquired = False
    awaitable_started = False
    benchmark_cancel_requested = False
    if durable_claim is not None:
        heartbeat = asyncio.create_task(_renew_start_lease(durable_claim))
    try:
        if heartbeat is None:
            await _eval_job_semaphore.acquire()
            slot_acquired = True
        else:
            slot_waiter = asyncio.create_task(_eval_job_semaphore.acquire())
            done, _ = await asyncio.wait(
                {slot_waiter, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done:
                if slot_waiter.done() and not slot_waiter.cancelled():
                    slot_acquired = bool(slot_waiter.result())
                await _raise_heartbeat_failure(heartbeat, durable_claim[3])
            slot_acquired = bool(await slot_waiter)

        benchmark = asyncio.create_task(awaitable)
        awaitable_started = True
        if heartbeat is None:
            await benchmark
        else:
            done, _ = await asyncio.wait(
                {benchmark, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done:
                try:
                    await _raise_heartbeat_failure(heartbeat, durable_claim[3])
                except rag_eval_store.RagEvalPersistenceFenceError as exc:
                    _discard_stale_live_job(durable_claim, str(exc))
                    benchmark_cancel_requested = True
                    await _cancel_benchmark(benchmark, durable_claim[3])
                    raise
            await benchmark
    except rag_eval_store.RagEvalPersistenceFenceError as exc:
        if durable_claim is not None:
            _discard_stale_live_job(durable_claim, str(exc))
        raise
    except BaseException as exc:
        if durable_claim is not None:
            _discard_stale_live_job(
                durable_claim,
                str(exc) or type(exc).__name__,
            )
        raise
    finally:
        if slot_waiter is not None and not slot_waiter.done():
            slot_waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await slot_waiter
        elif (
            slot_waiter is not None
            and not slot_waiter.cancelled()
            and not slot_acquired
        ):
            slot_acquired = bool(slot_waiter.result())
        if (
            benchmark is not None
            and not benchmark.done()
            and not benchmark_cancel_requested
        ):
            await _cancel_benchmark(benchmark, durable_claim[3] if durable_claim else "")
        if not awaitable_started:
            close = getattr(awaitable, "close", None)
            if close is not None:
                close()
        if slot_acquired:
            _eval_job_semaphore.release()
        if heartbeat is not None:
            heartbeat.cancel()
            with contextlib.suppress(
                asyncio.CancelledError,
                rag_eval_store.RagEvalPersistenceFenceError,
            ):
                await heartbeat
        if durable_claim is not None:
            try:
                expired = await asyncio.to_thread(
                    rag_eval_store.expire_rag_eval_start_request,
                    *durable_claim,
                )
            except Exception as exc:
                logger.warning(
                    "Could not expire RAG eval start lease for %s: %s",
                    durable_claim[3],
                    exc,
                )
            else:
                if not expired:
                    logger.info(
                        "RAG eval start lease was already replaced for %s",
                        durable_claim[3],
                    )


class RagEvalStartRequest(BaseModel):
    topic: str
    scope: str = "topic"
    n_questions: int = 20
    k: int = 8
    judge_mode: str = "standard"
    # Legacy callers that omit the new dimensions retain the old synthetic +
    # atomic behavior. The dashboard explicitly selects frozen + production.
    eval_kind: Literal["frozen_retrieval", "synthetic_e2e"] = "synthetic_e2e"
    retrieval_mode: Literal["atomic_dense", "production_replay"] = "atomic_dense"
    seed: int = Field(default=42, ge=0, le=2_147_483_647)


def _normalized_start_request(
    req: RagEvalStartRequest,
    n_questions: int,
    k: int,
    judge_mode: str,
) -> dict[str, str | int]:
    return {
        "topic": req.topic,
        "scope": req.scope,
        "n_questions": n_questions,
        "k": k,
        "judge_mode": judge_mode,
        "eval_kind": req.eval_kind,
        "retrieval_mode": req.retrieval_mode,
        "seed": req.seed,
    }


def _start_request_hash(request: dict[str, str | int]) -> str:
    canonical = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _inflight_key(user_id: str, request: dict[str, str | int]) -> str:
    return "|".join((
        user_id,
        str(request["topic"]),
        str(request["scope"]),
        str(request["eval_kind"]),
        str(request["retrieval_mode"]),
        str(request["n_questions"]),
        str(request["k"]),
        str(request["judge_mode"]),
        str(request["seed"]),
    ))


def _start_response(
    job_id: str,
    request: dict[str, str | int],
    *,
    estimated_llm_calls: int,
    reused: bool,
) -> dict:
    return {
        "job_id": job_id,
        "topic": request["topic"],
        "n_questions": request["n_questions"],
        "judge_mode": request["judge_mode"],
        "eval_kind": request["eval_kind"],
        "retrieval_mode": request["retrieval_mode"],
        "seed": request["seed"],
        "estimated_llm_calls": estimated_llm_calls,
        "reused": reused,
    }


def _start_response_from_job(job: dict, *, reused: bool) -> dict:
    request = {
        "topic": job.get("topic", ""),
        "scope": job.get("scope", "topic"),
        "n_questions": job.get("n_questions", 0),
        "k": job.get("k", 8),
        "judge_mode": job.get("judge_mode", "standard"),
        "eval_kind": job.get("eval_kind", "synthetic_e2e"),
        "retrieval_mode": job.get("retrieval_mode", "atomic_dense"),
        "seed": job.get("seed", 42),
    }
    return _start_response(
        str(job.get("job_id", "")),
        request,
        estimated_llm_calls=int(job.get("estimated_llm_calls") or 0),
        reused=reused,
    )


def _start_response_from_run(run: dict, *, reused: bool) -> dict:
    request = {
        "topic": run.get("topic", ""),
        "scope": run.get("scope", "topic"),
        "n_questions": int(run.get("n_questions") or 0),
        "k": int(run.get("k") or 8),
        "judge_mode": run.get("judge_mode", "standard"),
        "eval_kind": run.get("eval_kind", "synthetic_e2e"),
        "retrieval_mode": run.get("retrieval_mode", "atomic_dense"),
        "seed": int(run.get("seed") if run.get("seed") is not None else 42),
    }
    estimated_llm_calls = (
        int(request["n_questions"])
        * (6 + (int(request["k"]) if request["judge_mode"] == "full" else 0))
        if request["eval_kind"] == "synthetic_e2e"
        else 0
    )
    return _start_response(
        str(run.get("job_id", "")),
        request,
        estimated_llm_calls=estimated_llm_calls,
        reused=reused,
    )


def _start_response_from_mapping(
    mapping: dict,
    fallback_request: dict[str, str | int],
    *,
    fallback_estimated_llm_calls: int,
) -> dict:
    stored = mapping.get("response")
    metadata = stored if isinstance(stored, dict) else {}
    request = {
        key: metadata.get(key, fallback_request[key])
        for key in fallback_request
    }
    estimated = metadata.get(
        "estimated_llm_calls",
        fallback_estimated_llm_calls,
    )
    return _start_response(
        str(mapping["job_id"]),
        request,
        estimated_llm_calls=int(estimated or 0),
        reused=True,
    )


async def _resolved_durable_start_response(
    mapping: dict,
    user_id: str,
    fallback_request: dict[str, str | int],
    fallback_estimated_llm_calls: int,
) -> dict | None:
    job_id = str(mapping["job_id"])
    live_job = rag_eval_jobs[job_id] if job_id in rag_eval_jobs else None
    live_claim = live_job.get("_durable_claim") if live_job else None
    if (
        live_job
        and live_job.get("user_id") == user_id
        and mapping.get("lease_active") is True
        and isinstance(live_claim, tuple)
        and len(live_claim) == 5
        and live_claim[4] == mapping.get("claim_token")
    ):
        return _start_response_from_job(live_job, reused=True)
    stored = await asyncio.to_thread(
        rag_eval_store.get_rag_eval_run_by_job,
        job_id,
        user_id,
    )
    if stored is not None:
        return _start_response_from_run(stored, reused=True)
    if mapping.get("lease_active"):
        return _start_response_from_mapping(
            mapping,
            fallback_request,
            fallback_estimated_llm_calls=fallback_estimated_llm_calls,
        )
    return None


def _pending_status_from_mapping(mapping: dict) -> dict:
    stored = mapping.get("response")
    metadata = stored if isinstance(stored, dict) else {}
    return {
        "job_id": mapping["job_id"],
        "topic": metadata.get("topic", ""),
        "status": "pending",
        "phase": "pending",
        "done": 0,
        "total": 0,
        "n_questions": metadata.get("n_questions"),
        "judge_mode": metadata.get("judge_mode", "standard"),
        "eval_kind": metadata.get("eval_kind", "synthetic_e2e"),
        "retrieval_mode": metadata.get("retrieval_mode", "atomic_dense"),
        "seed": metadata.get("seed", 42),
        "estimated_llm_calls": metadata.get("estimated_llm_calls", 0),
        "error": None,
        "summary": None,
        "detail": None,
        "manifest": None,
        "run_id": None,
        "started_at": None,
        "updated_at": None,
    }


def _public_job(job: dict) -> dict:
    """对外暴露的进度视图（剔除 user_id 等内部键）。"""
    return {
        "job_id": job.get("job_id"),
        "topic": job.get("topic"),
        "status": job.get("status"),
        "phase": job.get("phase"),
        "done": job.get("done", 0),
        "total": job.get("total", 0),
        "n_questions": job.get("n_questions"),
        "judge_mode": job.get("judge_mode"),
        "eval_kind": job.get("eval_kind", "synthetic_e2e"),
        "retrieval_mode": job.get("retrieval_mode", "atomic_dense"),
        "seed": job.get("seed", 42),
        "estimated_llm_calls": job.get("estimated_llm_calls", 0),
        "error": job.get("error"),
        "summary": job.get("summary"),
        "detail": job.get("detail"),
        "manifest": job.get("manifest"),
        "run_id": job.get("run_id"),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
    }


def _finish_eval_task(task: asyncio.Task, key: str, job_id: str) -> None:
    _eval_tasks.discard(task)
    if _inflight.get(key) == job_id:
        _inflight.pop(key, None)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "RAG eval background task %s failed",
            job_id,
            exc_info=(type(exc), exc, exc.__traceback__),
        )


@router.post("/rag-eval/start")
async def start_rag_eval(
    req: RagEvalStartRequest,
    user_id: str = Depends(get_current_user),
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=200,
            pattern=r"^[\x21-\x7e]+$",
        ),
    ] = None,
):
    n = max(1, min(50, req.n_questions))
    k = max(1, min(20, req.k))
    judge_mode = (
        req.judge_mode if req.judge_mode in ("standard", "full") else "standard"
    ) if req.eval_kind == "synthetic_e2e" else "none"
    # Hash only normalized client inputs. Dataset availability may change after
    # the first call, but that must not turn a retry of the same request into a
    # 409 or silently point the key at a different job.
    request_identity = _normalized_start_request(req, n, k, judge_mode)
    request_hash = _start_request_hash(request_identity)
    identity_estimated_llm_calls = (
        n * (6 + (k if judge_mode == "full" else 0))
        if req.eval_kind == "synthetic_e2e" else 0
    )
    durable_mapping: dict | None = None
    if idempotency_key:
        durable_mapping = await asyncio.to_thread(
            rag_eval_store.get_rag_eval_start_request,
            user_id,
            idempotency_key,
        )
        if durable_mapping is not None:
            if durable_mapping["request_hash"] != request_hash:
                raise HTTPException(409, "幂等键已用于不同的 RAG 评测请求")
            replay = await _resolved_durable_start_response(
                durable_mapping,
                user_id,
                request_identity,
                identity_estimated_llm_calls,
            )
            if replay is not None:
                return replay

    topics = load_topics(user_id)
    if req.topic not in topics:
        raise HTTPException(400, f"Unknown topic: {req.topic}")

    if req.eval_kind == "frozen_retrieval":
        from backend.eval.rag_benchmark import load_frozen_cases
        try:
            selected_cases, _, _ = await asyncio.to_thread(
                load_frozen_cases, req.topic, n,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"固定评测集不可用: {str(exc)[:160]}") from exc
        if not selected_cases:
            raise HTTPException(422, f"固定评测集尚未覆盖 topic: {req.topic}")
        n = len(selected_cases)
    # The synthetic endpoint spends several LLM calls per case. Keep it owner
    # only; the frozen retrieval suite is deterministic and can diagnose a
    # user's own topic without granting a general-purpose token sink.
    if req.eval_kind == "synthetic_e2e":
        from backend.auth import is_owner
        if not is_owner(user_id):
            raise HTTPException(403, "合成式端到端评测仅限管理员")
    estimated_llm_calls = (
        n * (6 + (k if judge_mode == "full" else 0))
        if req.eval_kind == "synthetic_e2e" else 0
    )
    if estimated_llm_calls > MAX_EVAL_LLM_CALLS:
        raise HTTPException(
            422,
            f"预计 {estimated_llm_calls} 次 LLM 调用，超过单次预算 {MAX_EVAL_LLM_CALLS}；"
            "请减少题量、K 或改用标准评判",
        )

    execution_request = _normalized_start_request(req, n, k, judge_mode)
    key = _inflight_key(user_id, execution_request)
    existing = _inflight.get(key)
    existing_job = rag_eval_jobs[existing] if existing and existing in rag_eval_jobs else None
    if existing_job and existing_job.get("status") not in ("pending", "running"):
        existing_job = None

    durable_claim: tuple[str, str, str, str, str] | None = None
    start_was_reused = False
    if idempotency_key:
        response_metadata = {
            **execution_request,
            "estimated_llm_calls": estimated_llm_calls,
        }
        if durable_mapping is None:
            claim_state, job_id, claim_token = await asyncio.to_thread(
                rag_eval_store.claim_rag_eval_start_request,
                user_id,
                idempotency_key,
                request_hash,
                uuid.uuid4().hex[:12],
                response=response_metadata,
                lease_for=timedelta(seconds=_START_LEASE_DURATION_SECONDS),
            )
            if claim_state == "conflict":
                raise HTTPException(409, "幂等键已用于不同的 RAG 评测请求")
            if claim_state == "pending":
                raise HTTPException(503, "评测启动状态暂不可用，请重试")
            if claim_state == "replay":
                durable_mapping = await asyncio.to_thread(
                    rag_eval_store.get_rag_eval_start_request,
                    user_id,
                    idempotency_key,
                )
        else:
            claim_state = "replay"
            job_id = str(durable_mapping["job_id"])
            claim_token = None

        if claim_state == "replay":
            start_was_reused = True
            if durable_mapping is None:
                raise HTTPException(503, "评测启动状态暂不可用，请重试")
            replay = await _resolved_durable_start_response(
                durable_mapping,
                user_id,
                execution_request,
                estimated_llm_calls,
            )
            if replay is not None:
                return replay
            claim_token = await asyncio.to_thread(
                rag_eval_store.reclaim_rag_eval_start_request,
                user_id,
                idempotency_key,
                request_hash,
                job_id,
                response=response_metadata,
                lease_for=timedelta(seconds=_START_LEASE_DURATION_SECONDS),
            )
            if claim_token is None:
                # The lease may have been reclaimed after our read. Re-read it
                # so the response uses the winning worker's original metadata.
                durable_mapping = await asyncio.to_thread(
                    rag_eval_store.get_rag_eval_start_request,
                    user_id,
                    idempotency_key,
                )
                if durable_mapping is not None:
                    replay = await _resolved_durable_start_response(
                        durable_mapping,
                        user_id,
                        execution_request,
                        estimated_llm_calls,
                    )
                    if replay is not None:
                        return replay
                raise HTTPException(503, "评测启动租约暂不可用，请重试")

        if claim_token is None:
            raise HTTPException(503, "评测启动租约暂不可用，请重试")
        durable_claim = (
            user_id,
            idempotency_key,
            request_hash,
            job_id,
            claim_token,
        )
    else:
        if existing_job:
            return _start_response_from_job(existing_job, reused=True)
        job_id = uuid.uuid4().hex[:12]

    if len(_eval_tasks) >= MAX_QUEUED_EVAL_JOBS:
        if durable_claim is not None:
            await asyncio.to_thread(
                rag_eval_store.expire_rag_eval_start_request,
                *durable_claim,
            )
        raise HTTPException(429, "评测队列已满，请稍后重试")

    now = time.time()
    job = {
        "job_id": job_id, "user_id": user_id, "topic": req.topic, "scope": req.scope,
        "k": k, "n_questions": n, "judge_mode": judge_mode,
        "eval_kind": req.eval_kind, "retrieval_mode": req.retrieval_mode,
        "seed": req.seed,
        "estimated_llm_calls": estimated_llm_calls,
        "status": "pending", "phase": "pending", "done": 0, "total": 0,
        "started_at": now, "updated_at": now,
        "error": None, "summary": None, "run_id": None,
    }
    if durable_claim is not None:
        job["_durable_claim"] = durable_claim
    rag_eval_jobs[job_id] = job
    _inflight[key] = job_id

    if req.eval_kind == "frozen_retrieval":
        from backend.eval.rag_benchmark import run_frozen_benchmark
        task = asyncio.create_task(_run_with_global_slot(
            run_frozen_benchmark(
                job, req.topic, user_id, n, k, req.retrieval_mode, req.seed,
            ),
            durable_claim,
        ))
    else:
        task = asyncio.create_task(_run_with_global_slot(
            run_eval(
                job, req.topic, user_id, n, k, judge_mode,
                req.retrieval_mode, req.seed,
            ),
            durable_claim,
        ))
    _eval_tasks.add(task)
    task.add_done_callback(
        lambda completed, request_key=key, current_job_id=job_id: _finish_eval_task(
            completed, request_key, current_job_id,
        )
    )

    return _start_response(
        job_id,
        execution_request,
        estimated_llm_calls=estimated_llm_calls,
        reused=start_was_reused,
    )


@router.get("/rag-eval/status/{job_id}")
async def rag_eval_status(job_id: str, user_id: str = Depends(get_current_user)):
    live_job = rag_eval_jobs[job_id] if job_id in rag_eval_jobs else None
    live_claim = live_job.get("_durable_claim") if live_job else None
    if (
        live_job is not None
        and live_job.get("user_id") == user_id
        and isinstance(live_claim, tuple)
        and len(live_claim) == 5
    ):
        mapping = await asyncio.to_thread(
            rag_eval_store.get_rag_eval_start_request_by_job,
            job_id,
            user_id,
        )
        claim_is_current = bool(
            mapping is not None
            and mapping.get("lease_active") is True
            and mapping.get("claim_token") == live_claim[4]
        )
        if not claim_is_current:
            _discard_stale_live_job(
                live_claim,
                "RAG eval live job no longer owns an active lease",
            )
    if job_id not in rag_eval_jobs:
        # Completed runs survive a process/container restart in SQLite. Rebuild
        # a terminal status view so a client that was polling can finish cleanly.
        from backend.storage.rag_eval_store import get_rag_eval_run_by_job
        stored = await asyncio.to_thread(get_rag_eval_run_by_job, job_id, user_id)
        if not stored:
            mapping = await asyncio.to_thread(
                rag_eval_store.get_rag_eval_start_request_by_job,
                job_id,
                user_id,
            )
            if mapping is not None and mapping.get("lease_active"):
                return _pending_status_from_mapping(mapping)
            raise HTTPException(404, "Job not found")
        summary = {
            key: stored.get(key)
            for key in (
                "hit_at_k", "hit_at_k_strict", "mrr", "ndcg_at_k",
                "context_precision", "context_recall", "faithfulness",
                "answer_relevancy", "answer_correctness", "n_questions",
                "success_rate", "latency_p50_ms", "latency_p95_ms",
            )
        }
        n_questions = int(stored.get("n_questions") or 0)
        raw_success_rate = stored.get("success_rate")
        success_rate = (
            float(raw_success_rate) if raw_success_rate is not None else None
        )
        detail = stored.get("detail") or {}
        question_rows = detail.get("questions") or []
        statuses = []
        for row in question_rows:
            if not isinstance(row, dict):
                continue
            value = str(row.get("outcome") or row.get("retrieval_status") or "")
            if value:
                statuses.append(value)
        if len(statuses) == n_questions:
            evaluated = sum(status in {"ok", "empty", "degraded"} for status in statuses)
            if success_rate is None and n_questions:
                success_rate = evaluated / n_questions
        elif success_rate is not None:
            evaluated = round(n_questions * success_rate)
        else:
            evaluated = None
        degraded_count = sum(status == "degraded" for status in statuses)
        fully_healthy = sum(status in {"ok", "empty"} for status in statuses)
        manifest = stored.get("manifest") or {}
        dimensions = manifest.get("comparison_dimensions") or {}
        execution_profile = dimensions.get("execution_profile")
        state_stable = manifest.get("state_stable") is True
        observations = manifest.get("observations") or {}
        valid = bool(
            stored.get("status") == "completed"
            and success_rate is not None
            and success_rate >= 0.95
        )
        summary.update({
            "success_rate": success_rate,
            "evaluated_questions": evaluated,
            "error_count": (
                max(0, n_questions - evaluated) if evaluated is not None else None
            ),
            "degraded_count": degraded_count,
            "fully_healthy_rate": (
                fully_healthy / n_questions if len(statuses) == n_questions and n_questions else None
            ),
            "generation_success_rate": observations.get("generation_success_rate"),
            "judge_observed_rate": observations.get("judge_observed_rate"),
            "metric_observation_rate": observations.get("metric_observation_rate"),
            "valid": valid,
            "comparable": bool(
                valid and state_stable and execution_profile == "healthy"
            ),
        })
        terminal_completed = stored.get("status") == "completed"
        recovered_done = (
            n_questions if terminal_completed else int(detail.get("done") or 0)
        )
        recovered_total = int(detail.get("total") or n_questions)
        return {
            "job_id": job_id,
            "topic": stored.get("topic"),
            "status": stored.get("status", "completed"),
            "phase": stored.get("status", "completed"),
            "done": recovered_done,
            "total": recovered_total,
            "n_questions": stored.get("n_questions"),
            "judge_mode": stored.get("judge_mode"),
            "eval_kind": stored.get("eval_kind", "synthetic_e2e"),
            "retrieval_mode": stored.get("retrieval_mode", "atomic_dense"),
            "seed": stored.get("seed", 42),
            "error": stored.get("error"),
            "summary": summary,
            "detail": detail,
            "manifest": manifest,
            "run_id": stored.get("id"),
            "started_at": None,
            "updated_at": None,
        }
    job = rag_eval_jobs[job_id]
    if job.get("user_id") != user_id:
        raise HTTPException(404, "Job not found")
    return _public_job(job)


@router.get("/rag-eval/runs")
async def rag_eval_runs(
    topic: str | None = Query(default=None, max_length=200),
    eval_kind: str | None = Query(default=None, max_length=100),
    retrieval_mode: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user),
):
    from backend.storage.rag_eval_store import list_rag_eval_runs
    return await asyncio.to_thread(
        list_rag_eval_runs,
        user_id,
        topic,
        eval_kind,
        retrieval_mode,
        limit,
        offset,
    )


@router.get("/rag-eval/runs/{run_id}")
async def rag_eval_run_detail(
    run_id: int,
    user_id: str = Depends(get_current_user),
):
    from backend.storage.rag_eval_store import get_rag_eval_run
    run = await asyncio.to_thread(get_rag_eval_run, run_id, user_id)
    if not run:
        raise HTTPException(404, "评测记录不存在")
    return run
