"""AI assistant routes — chat, history, welcome."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.auth import get_current_user

router = APIRouter(prefix="/api")


class AssistantHistoryMessage(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be blank")
        return value


class AssistantChatRequest(BaseModel):
    message: str | None = Field(default=None, max_length=100_000)
    messages: list[AssistantHistoryMessage] = Field(
        default_factory=list, max_length=200,
    )

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value

    @model_validator(mode="after")
    def require_message(self):
        if self.message is None and not self.messages:
            raise ValueError("message or messages is required")
        return self


@router.post("/assistant/chat")
async def assistant_chat(
    req: AssistantChatRequest,
    user_id: str = Depends(get_current_user),
):
    from backend.assistant import stream_assistant_chat
    from backend.utils.sse_helpers import streaming_response

    # New format: {message: "text"} — server loads history
    # Legacy format: {messages: [...]} — extract last user message
    message = req.message
    if message is None:
        message = req.messages[-1].content

    return streaming_response(stream_assistant_chat(message, user_id))


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
