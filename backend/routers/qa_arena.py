"""问答演练场路由 — 会话管理 + 流式对话 + 总结导出。"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse

from backend.auth import get_current_user
from backend.storage import qa_sessions as store

router = APIRouter(prefix="/api/qa-arena")


@router.post("/sessions")
def create_session(body: dict = None, user_id: str = Depends(get_current_user)):
    body = body or {}
    title = body.get("title", "新对话")
    session = store.create_session(user_id, title)
    return session


@router.get("/sessions")
def list_sessions(limit: int = 50, offset: int = 0, user_id: str = Depends(get_current_user)):
    sessions = store.list_sessions(user_id, limit, offset)
    total = store.count_sessions(user_id)
    return {"sessions": sessions, "total": total}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, user_id: str = Depends(get_current_user)):
    if not store.delete_session(session_id, user_id):
        raise HTTPException(404, "会话不存在")
    from backend.qa_arena import delete_session_images
    delete_session_images(session_id, user_id)
    return {"ok": True}


@router.patch("/sessions/{session_id}")
def rename_session(session_id: str, body: dict, user_id: str = Depends(get_current_user)):
    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    if not store.update_session_title(session_id, user_id, title):
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: str, limit: int = 100, user_id: str = Depends(get_current_user)):
    messages = store.load_messages(session_id, user_id, limit)
    return {"messages": messages}


@router.delete("/sessions/{session_id}/messages")
def clear_messages(session_id: str, user_id: str = Depends(get_current_user)):
    store.clear_messages(session_id, user_id)
    from backend.qa_arena import delete_session_images
    delete_session_images(session_id, user_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/chat")
def chat(session_id: str, body: dict, user_id: str = Depends(get_current_user)):
    message = (body.get("message") or "").strip()
    images = body.get("images") or []
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
async def ingest_knowledge(session_id: str, body: dict = None, user_id: str = Depends(get_current_user)):
    """收录这次问答生成的知识卡片进 RAG 知识库（用户确认收录，人在环中）。

    取 body.content（前端传当前预览的卡片）；为空时回退读已保存的总结文件。
    分类 + 事实化清洗 + 写入用户知识库 + 增量嵌入，返回 {ok, topic, reason}。
    """
    if not store.get_session(session_id, user_id):
        raise HTTPException(404, "会话不存在")

    body = body or {}
    content = (body.get("content") or "").strip()
    if not content:
        from backend.qa_arena import get_summary_file
        saved = get_summary_file(session_id, user_id)
        if saved:
            content = saved[0]
    if not content:
        raise HTTPException(400, "没有可收录的卡片内容，请先生成知识卡片")

    from backend.knowledge_evolution import ingest_qa_card_to_knowledge
    return await ingest_qa_card_to_knowledge(content, user_id)


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
