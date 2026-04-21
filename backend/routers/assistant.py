"""AI assistant routes — chat, history, welcome."""
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from backend.auth import get_current_user

router = APIRouter(prefix="/api")


@router.post("/assistant/chat")
async def assistant_chat(req: dict, user_id: str = Depends(get_current_user)):
    from backend.assistant import stream_assistant_chat

    # New format: {message: "text"} — server loads history
    # Legacy format: {messages: [...]} — extract last user message
    message = req.get("message")
    if message is None:
        messages = req.get("messages", [])
        message = messages[-1]["content"] if messages else ""

    return StreamingResponse(
        stream_assistant_chat(message, user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/assistant/history")
async def assistant_history(user_id: str = Depends(get_current_user)):
    from backend.storage.assistant_chats import load_history
    return {"messages": load_history(user_id, limit=50)}


@router.delete("/assistant/history")
async def assistant_clear_history(user_id: str = Depends(get_current_user)):
    from backend.storage.assistant_chats import clear_history
    clear_history(user_id)
    return {"ok": True}


@router.get("/assistant/welcome")
async def assistant_welcome(user_id: str = Depends(get_current_user)):
    from backend.assistant import generate_welcome_back
    message = generate_welcome_back(user_id)
    return {"message": message}
