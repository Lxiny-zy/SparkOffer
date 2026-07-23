"""Algorithm solver routes."""
import asyncio
import threading
import weakref
from typing import Literal

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from pydantic import BaseModel, Field, model_validator

from backend.models import AlgorithmSolveRequest, AlgorithmChatRequest, AlgorithmSaveRequest
from backend.prompts.algorithm import ALGORITHM_SOLVE_SYSTEM, ALGORITHM_SOLVE_PROMPT, ALGORITHM_CHAT_SYSTEM
from backend.live_store import algorithm_sessions, save_live, get_live, del_live
from backend.storage.algorithm import (
    add_algorithm_card as _add_algo, list_algorithm_cards as _list_algo,
    get_algorithm_card as _get_algo, update_algorithm_card as _update_algo,
    delete_algorithm_card as _del_algo, get_algorithm_tags as _get_algo_tags,
    export_algorithm_cards as _export_algo,
)
from backend.auth import get_current_user
from backend.storage.sessions import new_session_id
from backend.utils.sse_helpers import stream_llm_sse, streaming_response, sse_event

router = APIRouter(prefix="/api")
MAX_EXPORT_ITEMS = 1_000


class AlgorithmCardUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    difficulty: str | None = Field(default=None, max_length=100)
    tags: list[str] | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=1_000_000)
    solution: str | None = Field(default=None, max_length=2_000_000)

    @model_validator(mode="after")
    def require_change(self):
        if all(
            value is None
            for value in (
                self.title, self.difficulty, self.tags, self.note, self.solution,
            )
        ):
            raise ValueError("at least one update field is required")
        return self


class AlgorithmExportRequest(BaseModel):
    format: Literal["json", "markdown"] = "json"
    ids: list[str] | None = Field(default=None, max_length=MAX_EXPORT_ITEMS)
    difficulty: str | None = Field(default=None, max_length=100)

_session_locks: "weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)
_session_locks_guard = threading.Lock()


def _get_session_lock(user_id: str, session_id: str) -> asyncio.Lock:
    key = (user_id, session_id)
    with _session_locks_guard:
        lock = _session_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _session_locks[key] = lock
        return lock


@router.post("/algorithm/solve")
def algorithm_solve(req: AlgorithmSolveRequest, user_id: str = Depends(get_current_user)):
    prompt = ALGORITHM_SOLVE_PROMPT.format(
        problem_text=req.problem_text, language=req.language,
    )
    messages = [
        SystemMessage(content=ALGORITHM_SOLVE_SYSTEM),
        HumanMessage(content=prompt),
    ]

    async def _gen():
        solution = ""
        async for kind, value in stream_llm_sse(messages, progress_prefix="正在解题", stream_content=True):
            if kind == "sse":
                yield value
            else:
                solution = value

        session_id = new_session_id()
        save_live(algorithm_sessions, session_id, "algorithm", user_id, {
            "user_id": user_id,
            "problem_text": req.problem_text,
            "language": req.language,
            "source_url": req.source_url,
            "messages": messages + [AIMessage(content=solution)],
            "solution": solution,
        })
        yield sse_event({"type": "complete", "data": {"session_id": session_id, "solution": solution}})
        yield sse_event({"type": "done"})

    return streaming_response(_gen())


@router.post("/algorithm/chat")
def algorithm_chat(req: AlgorithmChatRequest, user_id: str = Depends(get_current_user)):
    session = get_live(algorithm_sessions, req.session_id, "algorithm", user_id)
    if not session or session["user_id"] != user_id:
        raise HTTPException(404, "Algorithm session not found.")

    async def _gen():
        async with _get_session_lock(user_id, req.session_id):
            current = get_live(
                algorithm_sessions, req.session_id, "algorithm", user_id,
            )
            if not current or current.get("user_id") != user_id:
                yield sse_event({"type": "error", "message": "Algorithm session not found."})
                yield sse_event({"type": "done"})
                return
            current["messages"].append(HumanMessage(content=req.message))
            chat_system = ALGORITHM_CHAT_SYSTEM.format(language=current["language"])
            call_messages = [SystemMessage(content=chat_system)] + current["messages"][1:]

            ai_reply = ""
            async for kind, value in stream_llm_sse(
                call_messages, progress_prefix="正在思考", stream_content=True,
            ):
                if kind == "sse":
                    yield value
                else:
                    ai_reply = value

            current["messages"].append(AIMessage(content=ai_reply))
            save_live(
                algorithm_sessions, req.session_id, "algorithm", user_id, current,
            )
            yield sse_event({"type": "complete", "data": {
                "session_id": req.session_id, "message": ai_reply,
            }})
            yield sse_event({"type": "done"})

    return streaming_response(_gen())


@router.post("/algorithm/save")
async def algorithm_save(req: AlgorithmSaveRequest, user_id: str = Depends(get_current_user)):
    async with _get_session_lock(user_id, req.session_id):
        session = get_live(algorithm_sessions, req.session_id, "algorithm", user_id)
        if not session or session["user_id"] != user_id:
            raise HTTPException(404, "Algorithm session not found.")

        conversation = []
        for msg in session["messages"][1:]:
            role = "user" if isinstance(msg, HumanMessage) else "assistant"
            conversation.append({"role": role, "content": msg.content})

        card = _add_algo(
            user_id=user_id, title=req.title,
            problem_text=session["problem_text"], difficulty=req.difficulty,
            tags=req.tags, solution=session["solution"],
            conversation_history=conversation,
            source_url=session.get("source_url", ""),
            language=session["language"], note=req.note,
        )
        del_live(algorithm_sessions, req.session_id, user_id)
        return card


@router.get("/algorithm/cards")
def list_algorithm_cards_endpoint(
    difficulty: str = None, tag: str = None, search: str = None,
    sort_by: str = "created_at", sort_order: str = "desc",
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user),
):
    return _list_algo(
        user_id=user_id, difficulty=difficulty, tag=tag, search=search,
        sort_by=sort_by, sort_order=sort_order, limit=limit, offset=offset,
    )


@router.get("/algorithm/cards/{card_id}")
def get_algorithm_card_endpoint(card_id: str, user_id: str = Depends(get_current_user)):
    card = _get_algo(card_id, user_id=user_id)
    if not card:
        raise HTTPException(404, "Algorithm card not found.")
    return card


@router.put("/algorithm/cards/{card_id}")
def update_algorithm_card_endpoint(
    card_id: str, body: AlgorithmCardUpdateRequest,
    user_id: str = Depends(get_current_user),
):
    ok = _update_algo(
        card_id, user_id=user_id,
        title=body.title, difficulty=body.difficulty,
        tags=body.tags, note=body.note,
        solution=body.solution,
    )
    if not ok:
        raise HTTPException(404, "Algorithm card not found.")
    return {"ok": True}


@router.delete("/algorithm/cards/{card_id}")
def delete_algorithm_card_endpoint(card_id: str, user_id: str = Depends(get_current_user)):
    ok = _del_algo(card_id, user_id=user_id)
    if not ok:
        raise HTTPException(404, "Algorithm card not found.")
    return {"ok": True}


@router.get("/algorithm/tags")
def list_algorithm_tags(user_id: str = Depends(get_current_user)):
    return _get_algo_tags(user_id=user_id)


@router.post("/algorithm/export")
def export_algorithm_cards_endpoint(
    body: AlgorithmExportRequest,
    user_id: str = Depends(get_current_user),
):
    fmt = body.format
    if not body.ids:
        total = _list_algo(
            user_id=user_id, difficulty=body.difficulty, limit=1, offset=0,
        )["total"]
        if total > MAX_EXPORT_ITEMS:
            raise HTTPException(
                413,
                f"Export contains {total} items; select at most {MAX_EXPORT_ITEMS} at a time.",
            )
    content = _export_algo(
        user_id=user_id, ids=body.ids,
        difficulty=body.difficulty, fmt=fmt,
    )
    if fmt == "markdown":
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=sparkoffer-algorithm.md"},
        )
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=sparkoffer-algorithm.json"},
    )
