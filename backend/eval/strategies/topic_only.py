"""Topic-only strategy — uses the LLM with topic prompt, but no profile.

This is the middle baseline: it gets to use the same model the personalized
strategy uses, but with mastery=0 and weak_points=[]. If personalized beats
this strategy, we've isolated the effect of the persona context.
"""
from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from backend.eval.strategies.base import Strategy
from backend.llm_provider import get_langchain_llm

logger = logging.getLogger("uvicorn")


_TOPIC_ONLY_PROMPT = """请为 {topic_name} 主题生成 {n} 道面试题目。

要求：
1. 题目覆盖该主题的核心知识点
2. 难度均匀分布在 1-5 五个级别
3. 题型包含概念、应用、设计三类

只返回 JSON 数组，格式：
[
  {{"id": 1, "question": "...", "difficulty": 3, "category": "concept"}},
  ...
]"""


_TOPIC_DISPLAY = {
    "python": "Python 核心",
    "java": "Java 后端",
    "agent": "AI Agent 工程",
}


class TopicOnlyStrategy(Strategy):
    name = "topic_only"

    async def generate_questions(
        self, persona: dict, topic: str, n_questions: int = 10,
    ) -> list[dict]:
        topic_name = _TOPIC_DISPLAY.get(topic, topic)
        prompt = _TOPIC_ONLY_PROMPT.format(topic_name=topic_name, n=n_questions)

        llm = get_langchain_llm()
        try:
            response = await llm.ainvoke([
                SystemMessage(content="你是面试出题引擎。只返回 JSON 数组。"),
                HumanMessage(content=prompt),
            ])
        except Exception as e:
            logger.error("topic_only LLM call failed: %s", e)
            return []

        content = response.content.strip() if hasattr(response, "content") else str(response)
        # Strip markdown fence if present
        if "```" in content:
            parts = content.split("```")
            if len(parts) >= 2:
                content = parts[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

        # Find first [ and parse from there
        for i, c in enumerate(content):
            if c == "[":
                try:
                    parsed = json.loads(content[i:])
                    if isinstance(parsed, list):
                        out: list[dict] = []
                        for j, q in enumerate(parsed[:n_questions]):
                            if not isinstance(q, dict):
                                continue
                            q.setdefault("id", j + 1)
                            q.setdefault("difficulty", 3)
                            out.append(q)
                        return out
                except json.JSONDecodeError:
                    pass
                break

        logger.warning("topic_only: could not parse LLM JSON, returning empty")
        return []
