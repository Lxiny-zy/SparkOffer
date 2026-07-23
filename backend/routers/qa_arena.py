"""问答演练场路由 — 会话管理 + 流式对话 + 总结导出。"""

import asyncio
import hashlib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.auth import get_current_user
from backend.storage import qa_sessions as store

router = APIRouter(prefix="/api/qa-arena")
logger = logging.getLogger("uvicorn")

# Keep a strong reference when the HTTP waiter is cancelled. The actual
# ingestion must finish and persist its idempotency result because its disk
# write may already be running in asyncio.to_thread(), which is not cancellable.
_qa_ingest_tasks: set[asyncio.Task] = set()


def _observe_qa_ingest_task(task: asyncio.Task) -> None:
    _qa_ingest_tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("Detached QA knowledge ingestion failed: %s", error)


class QASessionCreateRequest(BaseModel):
    title: str = Field(default="新对话", max_length=200)


class QASessionRenameRequest(BaseModel):
    title: str = Field(max_length=200)


class QAChatRequest(BaseModel):
    message: str = Field(default="", max_length=100_000)
    images: list[str] = Field(default_factory=list, max_length=4)


class QAKnowledgeIngestRequest(BaseModel):
    content: str = Field(default="", max_length=1_000_000)


@router.post("/sessions")
def create_session(
    body: QASessionCreateRequest | None = None,
    user_id: str = Depends(get_current_user),
):
    title = (body.title if body else "新对话").strip() or "新对话"
    session = store.create_session(user_id, title)
    return session


@router.get("/sessions")
def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user),
):
    sessions = store.list_sessions(user_id, limit, offset)
    total = store.count_sessions(user_id)
    return {"sessions": sessions, "total": total}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, user_id: str = Depends(get_current_user)):
    outcome = store.delete_session_checked(session_id, user_id)
    if outcome == "busy":
        raise HTTPException(409, "知识卡片正在收录，请完成后再删除会话")
    if outcome == "missing":
        raise HTTPException(404, "会话不存在")
    from backend.qa_arena import delete_session_images
    delete_session_images(session_id, user_id)
    return {"ok": True}


@router.patch("/sessions/{session_id}")
def rename_session(
    session_id: str, body: QASessionRenameRequest,
    user_id: str = Depends(get_current_user),
):
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    if not store.update_session_title(session_id, user_id, title):
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
def get_messages(
    session_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    user_id: str = Depends(get_current_user),
):
    messages = store.load_messages(session_id, user_id, limit)
    return {"messages": messages}


@router.delete("/sessions/{session_id}/messages")
def clear_messages(session_id: str, user_id: str = Depends(get_current_user)):
    store.clear_messages(session_id, user_id)
    from backend.qa_arena import delete_session_images
    delete_session_images(session_id, user_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/chat")
def chat(
    session_id: str, body: QAChatRequest,
    user_id: str = Depends(get_current_user),
):
    message = body.message.strip()
    images = body.images
    if not message and not images:
        raise HTTPException(400, "消息不能为空")
    if not store.get_session(session_id, user_id):
        raise HTTPException(404, "会话不存在")

    from backend.qa_arena import stream_qa_chat
    return StreamingResponse(
        stream_qa_chat(session_id, message, user_id, images),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions/{session_id}/images/{name}")
def get_image(session_id: str, name: str, user_id: str = Depends(get_current_user)):
    """Serve a stored chat image (auth'd, owner-scoped, path-traversal safe).

    The frontend fetches this through the authenticated client (token in the
    Authorization header) and renders the blob — so the JWT never leaks into an
    <img> URL.
    """
    if not store.get_session(session_id, user_id):
        raise HTTPException(404, "会话不存在")
    from backend.qa_arena import get_image_path
    path = get_image_path(session_id, user_id, name)
    if not path:
        raise HTTPException(404, "图片不存在")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@router.post("/sessions/{session_id}/regenerate")
def regenerate(session_id: str, user_id: str = Depends(get_current_user)):
    """Re-answer the last user question (e.g. after a cut-off or empty reply).

    No request body: the prompt is the last stored user message. Any trailing
    assistant reply is dropped and replaced by the fresh answer.
    """
    if not store.get_session(session_id, user_id):
        raise HTTPException(404, "会话不存在")

    from backend.qa_arena import stream_qa_regenerate
    return StreamingResponse(
        stream_qa_regenerate(session_id, user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/summary")
def summary(session_id: str, effort: str = "", user_id: str = Depends(get_current_user)):
    if not store.get_session(session_id, user_id):
        raise HTTPException(404, "会话不存在")

    from backend.qa_arena import stream_generate_summary
    return StreamingResponse(
        stream_generate_summary(session_id, user_id, reasoning_effort=effort or None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/ingest-knowledge")
async def ingest_knowledge(
    session_id: str, body: QAKnowledgeIngestRequest | None = None,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=200,
            pattern=r"^[\x21-\x7e]+$",
        ),
    ] = None,
    user_id: str = Depends(get_current_user),
):
    """收录这次问答生成的知识卡片进 RAG 知识库（用户确认收录，人在环中）。

    取 body.content（前端传当前预览的卡片）；为空时回退读已保存的总结文件。
    分类 + 事实化清洗 + 写入用户知识库 + 增量嵌入，返回 {ok, topic, reason}。
    """
    if not store.get_session(session_id, user_id):
        raise HTTPException(404, "会话不存在")

    content = (body.content if body else "").strip()
    if not content:
        from backend.qa_arena import get_summary_file
        saved = get_summary_file(session_id, user_id)
        if saved:
            content = saved[0]
    if not content:
        raise HTTPException(400, "没有可收录的卡片内容，请先生成知识卡片")

    from backend.knowledge_evolution import ingest_qa_card_to_knowledge
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if not idempotency_key:
        idempotency_key = f"auto:{content_hash}"
    idempotency_marker = hashlib.sha256(
        f"{user_id}\0{session_id}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()
    claim, cached, claim_token = store.claim_ingest_request(
        session_id,
        user_id,
        idempotency_key,
        content_hash,
        idempotency_marker,
    )
    if claim == "complete" and cached is not None:
        return cached
    if claim == "conflict":
        raise HTTPException(409, "幂等键已用于不同的知识卡片内容")
    if claim == "pending":
        raise HTTPException(
            409,
            "该知识卡片正在收录，请稍后重试",
            headers={"Retry-After": "2"},
        )
    if claim == "missing":
        raise HTTPException(404, "会话不存在")
    if not claim_token:
        raise HTTPException(503, "无法获取收录幂等租约，请稍后重试")

    async def renew_claim() -> None:
        while True:
            await asyncio.sleep(30)
            try:
                renewed = store.renew_ingest_request(
                    session_id,
                    user_id,
                    idempotency_key,
                    content_hash,
                    claim_token,
                )
            except Exception as exc:
                # A transient SQLite busy/error must not suppress a successful
                # ingest. Retry on the next heartbeat; completion remains
                # token-scoped and will fail closed if another worker took over.
                logger.warning(
                    "Could not renew QA ingest lease for %s: %s",
                    session_id,
                    exc,
                )
                continue
            if not renewed:
                return

    async def execute_claimed_ingest() -> dict:
        renewal_task = asyncio.create_task(renew_claim())

        def abandon_failed_claim() -> None:
            try:
                store.abandon_ingest_request(
                    session_id,
                    user_id,
                    idempotency_key,
                    content_hash,
                    claim_token,
                )
            except Exception as cleanup_error:
                logger.error(
                    "Could not hand off failed QA ingest claim for %s: %s",
                    session_id,
                    cleanup_error,
                )

        try:
            try:
                result = await ingest_qa_card_to_knowledge(
                    content,
                    user_id,
                    idempotency_marker=idempotency_marker,
                    claim_token=claim_token,
                )
            except asyncio.CancelledError:
                # Leave the lease pending. A to_thread write may still finish
                # during shutdown, so releasing here could admit a duplicate.
                raise
            except Exception:
                abandon_failed_claim()
                raise

            if result.get("ok"):
                try:
                    completed = store.complete_ingest_request(
                        session_id,
                        user_id,
                        idempotency_key,
                        content_hash,
                        claim_token,
                        result,
                    )
                except Exception:
                    abandon_failed_claim()
                    raise
                if not completed:
                    raise HTTPException(
                        503,
                        "收录已完成，但幂等状态保存失败，请稍后重试",
                    )
            else:
                try:
                    store.release_ingest_request(
                        session_id,
                        user_id,
                        idempotency_key,
                        content_hash,
                        claim_token,
                    )
                except Exception:
                    abandon_failed_claim()
                    raise
            return result
        finally:
            renewal_task.cancel()
            try:
                await renewal_task
            except asyncio.CancelledError:
                pass

    operation = asyncio.create_task(execute_claimed_ingest())
    _qa_ingest_tasks.add(operation)
    operation.add_done_callback(_observe_qa_ingest_task)
    return await asyncio.shield(operation)


@router.get("/sessions/{session_id}/summary/download")
def download_summary(session_id: str, user_id: str = Depends(get_current_user)):
    from backend.qa_arena import get_summary_file
    result = get_summary_file(session_id, user_id)
    if not result:
        raise HTTPException(404, "暂无总结文件")
    content, filename = result
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
