"""RAG 评测路由 —— 按需触发的离线 RAGAS 基准评测（异步任务 + 进度轮询）。

刻意不走 embedding 任务队列（仅 2 worker、在索引写入关键路径上）：分钟级评测会
饿死嵌入写入。改用独立 asyncio.create_task，进度写入 live_store.rag_eval_jobs，
前端轮询 /status。结果落 rag_eval_runs 表。
"""
import asyncio
import time
import uuid

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from backend.indexer import load_topics
from backend.auth import get_current_user
from backend.live_store import rag_eval_jobs
from backend.rag_eval import run_eval

router = APIRouter(prefix="/api")

# 强引用在途任务，防止 asyncio 只持弱引用导致任务被 GC（同 main.warmup_task /
# embedding_tasks._bg_tasks 的做法）。
_eval_tasks: set[asyncio.Task] = set()
# (user_id, topic) → job_id，用于去重在途评测。
_inflight: dict[str, str] = {}


class RagEvalStartRequest(BaseModel):
    topic: str
    scope: str = "topic"
    n_questions: int = 20
    k: int = 8
    judge_mode: str = "standard"


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
        "error": job.get("error"),
        "summary": job.get("summary"),
        "detail": job.get("detail"),
        "run_id": job.get("run_id"),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
    }


@router.post("/rag-eval/start")
async def start_rag_eval(req: RagEvalStartRequest, user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if req.topic not in topics:
        raise HTTPException(400, f"Unknown topic: {req.topic}")

    n = max(1, min(50, req.n_questions))
    k = max(1, min(20, req.k))
    judge_mode = req.judge_mode if req.judge_mode in ("standard", "full") else "standard"

    # 去重：同 (user, topic) 已有在途评测则拒绝重复启动。
    key = f"{user_id}:{req.topic}"
    existing = _inflight.get(key)
    if existing and existing in rag_eval_jobs:
        prev = rag_eval_jobs[existing]
        if prev.get("status") in ("pending", "running"):
            raise HTTPException(409, "该主题已有评测在进行中，请等待完成")

    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    job = {
        "job_id": job_id, "user_id": user_id, "topic": req.topic, "scope": req.scope,
        "k": k, "n_questions": n, "judge_mode": judge_mode,
        "status": "pending", "phase": "pending", "done": 0, "total": 0,
        "started_at": now, "updated_at": now,
        "error": None, "summary": None, "run_id": None,
    }
    rag_eval_jobs[job_id] = job
    _inflight[key] = job_id

    task = asyncio.create_task(run_eval(job, req.topic, user_id, n, k, judge_mode))
    _eval_tasks.add(task)
    task.add_done_callback(_eval_tasks.discard)

    return {"job_id": job_id, "topic": req.topic, "n_questions": n, "judge_mode": judge_mode}


@router.get("/rag-eval/status/{job_id}")
async def rag_eval_status(job_id: str, user_id: str = Depends(get_current_user)):
    if job_id not in rag_eval_jobs:
        raise HTTPException(404, "Job not found")
    job = rag_eval_jobs[job_id]
    if job.get("user_id") != user_id:
        raise HTTPException(404, "Job not found")
    return _public_job(job)


@router.get("/rag-eval/runs")
async def rag_eval_runs(
    topic: str = None, limit: int = 20, user_id: str = Depends(get_current_user),
):
    from backend.storage.rag_eval_store import list_rag_eval_runs
    return await asyncio.to_thread(list_rag_eval_runs, user_id, topic, max(1, min(100, limit)), 0)
