"""Algorithm solver routes."""
import uuid

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from backend.models import AlgorithmSolveRequest, AlgorithmChatRequest, AlgorithmSaveRequest
from backend.prompts.algorithm import ALGORITHM_SOLVE_SYSTEM, ALGORITHM_SOLVE_PROMPT, ALGORITHM_CHAT_SYSTEM
from backend.live_store import algorithm_sessions, save_live, get_live
from backend.storage.algorithm import (
    add_algorithm_card as _add_algo, list_algorithm_cards as _list_algo,
    get_algorithm_card as _get_algo, update_algorithm_card as _update_algo,
    delete_algorithm_card as _del_algo, get_algorithm_tags as _get_algo_tags,
    export_algorithm_cards as _export_algo,
)
from backend.auth import get_current_user
from backend.utils.sse_helpers import stream_llm_sse, streaming_response, sse_event

router = APIRouter(prefix="/api")


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
        async for kind, value in stream_llm_sse(messages, progress_prefix="正在解题"):
            if kind == "sse":
                yield value
            else:
                solution = value

        session_id = uuid.uuid4().hex[:12]
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

    session["messages"].append(HumanMessage(content=req.message))
    chat_system = ALGORITHM_CHAT_SYSTEM.format(language=session["language"])
    call_messages = [SystemMessage(content=chat_system)] + session["messages"][1:]

    async def _gen():
        ai_reply = ""
        async for kind, value in stream_llm_sse(call_messages, progress_prefix="正在思考"):
            if kind == "sse":
                yield value
            else:
                ai_reply = value

        session["messages"].append(AIMessage(content=ai_reply))
        session["solution"] = ai_reply
        save_live(algorithm_sessions, req.session_id, "algorithm", user_id, session)
        yield sse_event({"type": "complete", "data": {"session_id": req.session_id, "message": ai_reply}})
        yield sse_event({"type": "done"})

    return streaming_response(_gen())


@router.post("/algorithm/save")
def algorithm_save(req: AlgorithmSaveRequest, user_id: str = Depends(get_current_user)):
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
    algorithm_sessions.pop(req.session_id, None)
    return card


@router.get("/algorithm/cards")
def list_algorithm_cards_endpoint(
    difficulty: str = None, tag: str = None, search: str = None,
    sort_by: str = "created_at", sort_order: str = "desc",
    limit: int = 50, offset: int = 0,
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
def update_algorithm_card_endpoint(card_id: str, body: dict, user_id: str = Depends(get_current_user)):
    ok = _update_algo(
        card_id, user_id=user_id,
        title=body.get("title"), difficulty=body.get("difficulty"),
        tags=body.get("tags"), note=body.get("note"),
        solution=body.get("solution"),
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
def export_algorithm_cards_endpoint(body: dict, user_id: str = Depends(get_current_user)):
    fmt = body.get("format", "json")
    content = _export_algo(
        user_id=user_id, ids=body.get("ids"),
        difficulty=body.get("difficulty"), fmt=fmt,
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
