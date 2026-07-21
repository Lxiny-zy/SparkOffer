"""RAG 评测路由 —— 按需触发的离线 RAGAS 基准评测（异步任务 + 进度轮询）。

刻意不走 embedding 任务队列（仅 2 worker、在索引写入关键路径上）：分钟级评测会
饿死嵌入写入。改用独立 asyncio.create_task，进度写入 live_store.rag_eval_jobs，
前端轮询 /status。结果落 rag_eval_runs 表。
"""
import asyncio
import time
import uuid

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Literal

from backend.indexer import load_topics
from backend.auth import get_current_user
from backend.live_store import rag_eval_jobs
from backend.rag_eval import run_eval

router = APIRouter(prefix="/api")

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


async def _run_with_global_slot(awaitable) -> None:
    async with _eval_job_semaphore:
        await awaitable


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


@router.post("/rag-eval/start")
async def start_rag_eval(req: RagEvalStartRequest, user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if req.topic not in topics:
        raise HTTPException(400, f"Unknown topic: {req.topic}")

    n = max(1, min(50, req.n_questions))
    k = max(1, min(20, req.k))
    judge_mode = (
        req.judge_mode if req.judge_mode in ("standard", "full") else "standard"
    ) if req.eval_kind == "synthetic_e2e" else "none"
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
    # Idempotent retry: return the existing job for the same user/topic.
    key = "|".join((
        user_id,
        req.topic,
        req.eval_kind,
        req.retrieval_mode,
        str(n),
        str(k),
        judge_mode,
        str(req.seed),
    ))
    existing = _inflight.get(key)
    if existing and existing in rag_eval_jobs:
        prev = rag_eval_jobs[existing]
        if prev.get("status") in ("pending", "running"):
            return {
                "job_id": existing,
                "topic": prev.get("topic", req.topic),
                "n_questions": prev.get("n_questions"),
                "judge_mode": prev.get("judge_mode"),
                "eval_kind": prev.get("eval_kind"),
                "retrieval_mode": prev.get("retrieval_mode"),
                "seed": prev.get("seed"),
                "estimated_llm_calls": prev.get("estimated_llm_calls", 0),
                "reused": True,
            }
    if len(_eval_tasks) >= MAX_QUEUED_EVAL_JOBS:
        raise HTTPException(429, "评测队列已满，请稍后重试")

    job_id = uuid.uuid4().hex[:12]
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
    rag_eval_jobs[job_id] = job
    _inflight[key] = job_id

    if req.eval_kind == "frozen_retrieval":
        from backend.eval.rag_benchmark import run_frozen_benchmark
        task = asyncio.create_task(_run_with_global_slot(
            run_frozen_benchmark(
                job, req.topic, user_id, n, k, req.retrieval_mode, req.seed,
            )
        ))
    else:
        task = asyncio.create_task(_run_with_global_slot(
            run_eval(
                job, req.topic, user_id, n, k, judge_mode,
                req.retrieval_mode, req.seed,
            )
        ))
    _eval_tasks.add(task)
    task.add_done_callback(
        lambda completed, request_key=key, current_job_id=job_id: _finish_eval_task(
            completed, request_key, current_job_id,
        )
    )

    return {
        "job_id": job_id,
        "topic": req.topic,
        "n_questions": n,
        "judge_mode": judge_mode,
        "eval_kind": req.eval_kind,
        "retrieval_mode": req.retrieval_mode,
        "seed": req.seed,
        "estimated_llm_calls": estimated_llm_calls,
        "reused": False,
    }


@router.get("/rag-eval/status/{job_id}")
async def rag_eval_status(job_id: str, user_id: str = Depends(get_current_user)):
    if job_id not in rag_eval_jobs:
        # Completed runs survive a process/container restart in SQLite. Rebuild
        # a terminal status view so a client that was polling can finish cleanly.
        from backend.storage.rag_eval_store import get_rag_eval_run_by_job
        stored = await asyncio.to_thread(get_rag_eval_run_by_job, job_id, user_id)
        if not stored:
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
    topic: str = None,
    eval_kind: str = None,
    retrieval_mode: str = None,
    limit: int = 20,
    offset: int = 0,
    user_id: str = Depends(get_current_user),
):
    from backend.storage.rag_eval_store import list_rag_eval_runs
    return await asyncio.to_thread(
        list_rag_eval_runs,
        user_id,
        topic,
        eval_kind,
        retrieval_mode,
        max(1, min(100, limit)),
        max(0, offset),
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
