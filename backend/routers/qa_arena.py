"""问答演练场路由 — 会话管理 + 流式对话 + 总结导出。"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse

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
    return {"ok": True}


@router.patch("/sessions/{session_id}")
def rename_session(session_id: str, body: dict, user_id: str = Depends(get_current_user)):
    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    store.update_session_title(session_id, user_id, title)
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: str, limit: int = 100, user_id: str = Depends(get_current_user)):
    messages = store.load_messages(session_id, user_id, limit)
    return {"messages": messages}


@router.delete("/sessions/{session_id}/messages")
def clear_messages(session_id: str, user_id: str = Depends(get_current_user)):
    store.clear_messages(session_id, user_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/chat")
def chat(session_id: str, body: dict, user_id: str = Depends(get_current_user)):
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "消息不能为空")
    if not store.get_session(session_id, user_id):
        raise HTTPException(404, "会话不存在")

    from backend.qa_arena import stream_qa_chat
    return StreamingResponse(
        stream_qa_chat(session_id, message, user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/summary")
def summary(session_id: str, user_id: str = Depends(get_current_user)):
    if not store.get_session(session_id, user_id):
        raise HTTPException(404, "会话不存在")

    from backend.qa_arena import stream_generate_summary
    return StreamingResponse(
        stream_generate_summary(session_id, user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
