"""Knowledge training routes."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth import get_current_user
from backend.indexer import load_topics
from backend.knowledge_training import (
    SUPPORTED_DEPTHS,
    SUPPORTED_MODES,
    build_llm_messages,
    normalize_training_cards,
    sample_topic_sections,
    topic_availability,
)
from backend.storage.knowledge_cards import (
    FAMILIARITY_SCORE,
    covered_section_keys,
    get_due_cards,
    list_cards,
    record_review,
    topic_due_counts,
    topic_saved_counts,
    upsert_cards,
)
from backend.utils.sse_helpers import sse_event, stream_llm_sse, streaming_response

router = APIRouter(prefix="/api/knowledge-training", tags=["knowledge-training"])


class KnowledgeTrainingCardsRequest(BaseModel):
    topic: str
    count: int = Field(default=5, ge=1, le=10)
    mode: str = "random"
    depth: str = "understand"
    seed: str | None = None


class ReviewRequest(BaseModel):
    card_id: str
    familiarity: str


@router.get("/availability")
async def get_availability(user_id: str = Depends(get_current_user)):
    avail = await asyncio.to_thread(topic_availability, user_id)
    due = await asyncio.to_thread(topic_due_counts, user_id)
    saved = await asyncio.to_thread(topic_saved_counts, user_id)
    for key, info in avail.get("topics", {}).items():
        info["due_count"] = due.get(key, 0)
        info["saved_count"] = saved.get(key, 0)
    return avail


@router.get("/cards/saved")
async def get_saved_cards(
    topic: str | None = None,
    familiarity: str | None = None,
    user_id: str = Depends(get_current_user),
):
    return await asyncio.to_thread(
        list_cards, user_id=user_id, topic=topic, familiarity=familiarity, limit=500
    )


@router.get("/due")
async def get_due(
    topic: str | None = None,
    user_id: str = Depends(get_current_user),
):
    cards = await asyncio.to_thread(get_due_cards, user_id=user_id, topic=topic, limit=100)
    return {"cards": cards, "total": len(cards)}


@router.post("/review")
async def submit_review(body: ReviewRequest, user_id: str = Depends(get_current_user)):
    if body.familiarity not in FAMILIARITY_SCORE:
        raise HTTPException(400, "未知熟悉度等级")
    card = await asyncio.to_thread(
        record_review, user_id=user_id, card_id=body.card_id, familiarity=body.familiarity
    )
    if card is None:
        raise HTTPException(404, "卡片不存在")
    return {"card": card}


@router.post("/cards")
async def generate_cards(
    body: KnowledgeTrainingCardsRequest,
    user_id: str = Depends(get_current_user),
):
    async def _gen():
        if body.mode not in SUPPORTED_MODES:
            yield sse_event({"type": "error", "message": "当前版本仅支持随机知识点和高频考点模式"})
            return
        if body.depth not in SUPPORTED_DEPTHS:
            yield sse_event({"type": "error", "message": "未知训练深度"})
            return

        topics = load_topics(user_id)
        if body.topic not in topics:
            yield sse_event({"type": "error", "message": "模块不存在"})
            return

        yield sse_event({"type": "progress", "message": "正在抽取知识片段..."})
        try:
            # Steer sampling away from sections already turned into cards, so a new
            # round covers fresh knowledge rather than re-carding the same areas.
            covered = await asyncio.to_thread(covered_section_keys, user_id, body.topic)
            candidate_count = min(20, int(body.count * 1.5) + 3)
            sections, effective_seed = await asyncio.to_thread(
                sample_topic_sections,
                user_id,
                body.topic,
                candidate_count,
                body.mode,
                body.seed,
                covered,
            )
        except Exception as exc:
            import logging
            logging.getLogger("uvicorn").error(
                "Knowledge training section sampling failed (%s)", type(exc).__name__,
            )
            yield sse_event({"type": "error", "message": "知识片段暂时不可用，请稍后重试"})
            return

        if not sections:
            yield sse_event({
                "type": "error",
                "message": "该模块还没有可训练的知识片段，请先在知识库上传文件或生成基础知识。",
            })
            return

        topic_name = topics[body.topic].get("name", body.topic)
        messages = build_llm_messages(topic_name, body.depth, sections, body.count)
        raw = ""
        async for kind, value in stream_llm_sse(messages, progress_prefix="正在生成训练卡片"):
            if kind == "sse":
                yield value
            elif kind == "error":
                return
            else:
                raw = value

        cards = normalize_training_cards(raw, topic=body.topic, sections=sections)[:body.count]
        if not cards:
            yield sse_event({"type": "error", "message": "训练卡片解析失败，请稍后重试。"})
            return

        # Persist (exact dedup via PK) and return the stored rows so the client gets
        # each card's current familiarity / spaced-repetition state.
        stored = await asyncio.to_thread(upsert_cards, user_id, body.topic, body.depth, cards)
        if not stored:
            stored = cards

        for card in stored:
            yield sse_event({"type": "card", "data": card})

        result = {"cards": stored, "total": len(stored), "seed": effective_seed}
        yield sse_event({"type": "complete", "data": result})
        yield sse_event({"type": "done", "total": len(stored)})

    return streaming_response(_gen())
