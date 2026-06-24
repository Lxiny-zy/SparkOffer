"""Knowledge training routes."""

import asyncio
import json

from fastapi import APIRouter, Depends
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
from backend.utils.sse_helpers import sse_event, stream_llm_sse, streaming_response

router = APIRouter(prefix="/api/knowledge-training", tags=["knowledge-training"])


class KnowledgeTrainingCardsRequest(BaseModel):
    topic: str
    count: int = Field(default=5, ge=1, le=10)
    mode: str = "random"
    depth: str = "understand"
    seed: str | None = None


@router.get("/availability")
async def get_availability(user_id: str = Depends(get_current_user)):
    return await asyncio.to_thread(topic_availability, user_id)


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
            candidate_count = min(20, int(body.count * 1.5) + 3)
            sections, effective_seed = await asyncio.to_thread(
                sample_topic_sections,
                user_id,
                body.topic,
                candidate_count,
                body.mode,
                body.seed,
            )
        except Exception as exc:
            yield sse_event({"type": "error", "message": str(exc)})
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
        llm_failed = False
        async for kind, value in stream_llm_sse(messages, progress_prefix="正在生成训练卡片"):
            if kind == "sse":
                yield value
                try:
                    event = json.loads(value.removeprefix("data: ").strip())
                    if event.get("type") == "error":
                        llm_failed = True
                except Exception:
                    pass
            else:
                raw = value

        if llm_failed:
            return

        cards = normalize_training_cards(raw, topic=body.topic, sections=sections)[:body.count]
        if not cards:
            yield sse_event({"type": "error", "message": "训练卡片解析失败，请稍后重试。"})
            return

        for card in cards:
            yield sse_event({"type": "card", "data": card})

        result = {"cards": cards, "total": len(cards), "seed": effective_seed}
        yield sse_event({"type": "complete", "data": result})
        yield sse_event({"type": "done", "total": len(cards)})

    return streaming_response(_gen())
