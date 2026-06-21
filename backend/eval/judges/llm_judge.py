"""LLM-as-judge with multi-channel tri-model voting.

We grab up to 3 distinct channels from ``channel_manager.get_all_channels("llm")``,
prefer distinct ``model`` names to avoid same-model bias, and ask each to rate
the question set on a 1-10 scale. Final score = median(scores) / 10.

Fail-soft: any channel that fails or returns un-parseable JSON is skipped.
If fewer than 1 channel succeeds we return (0.0, "all_channels_failed").
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from statistics import median

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from backend.channel_manager import get_all_channels
from backend.ai_config import get_tuning

logger = logging.getLogger("uvicorn")


_JUDGE_PROMPT = """你是面试题质量评审。下面给定候选人画像和一组生成的题目，请评估这些题目对该候选人的针对性。

## 候选人画像
- 目标岗位：{target_role}
- 最大主题掌握度：{mastery}/100
- 薄弱点：
{weak_points}
- 沟通风格：{communication_style}
- 偏好难度：{preferred_difficulty}

## 候选题目（10 道）
{questions}

## 评分维度
1. **薄弱点针对性**：题目是否聚焦候选人的真实薄弱点，而非泛泛而谈
2. **难度匹配**：难度是否与掌握度匹配（新手不要扔进设计题，资深不要问背诵题）
3. **覆盖多样性**：10 道题是否覆盖不同子领域，避免重复同一知识点
4. **可答性**：是否是真实可答的面试题，而非含糊或不可量化的"开放题"

## 输出
只返回 JSON：{{"score": <1-10 整数或一位小数>, "reasoning": "<不超过 80 字的理由>"}}
"""


def _select_distinct_channels(max_n: int = 3) -> list[dict]:
    """Pick up to max_n channels with distinct model names."""
    pool = [c for c in get_all_channels("llm") if c.get("enabled", True)]
    seen_models: set[str] = set()
    chosen: list[dict] = []
    for ch in pool:
        model = (ch.get("model") or "").strip()
        if model and model not in seen_models:
            seen_models.add(model)
            # Resolve key — use first key in the channel
            ch_copy = dict(ch)
            keys = ch.get("keys", [])
            ch_copy["api_key"] = keys[0] if keys else ch.get("api_key", "")
            chosen.append(ch_copy)
        if len(chosen) >= max_n:
            break
    # If we have no channels at all (single-channel legacy config), return empty
    # and the caller will fall back to one ainvoke via the default LLM.
    return chosen


def _format_persona_brief(persona: dict) -> dict:
    """Extract a brief slice of the persona for the prompt."""
    masteries = persona.get("topic_mastery", {})
    max_mastery = 0
    for m in masteries.values():
        if isinstance(m, dict):
            s = m.get("score", m.get("level", 0) * 20)
            if s and s > max_mastery:
                max_mastery = s

    wps = persona.get("weak_points", [])
    wp_lines = []
    for w in wps[:8]:
        point = w.get("point", "") if isinstance(w, dict) else str(w)
        if point:
            wp_lines.append(f"- {point}")

    return {
        "target_role": persona.get("target_role", "通用候选人"),
        "mastery": int(max_mastery),
        "weak_points": "\n".join(wp_lines) or "（暂无）",
        "communication_style": persona.get("communication", {}).get("style", "（暂无）"),
        "preferred_difficulty": persona.get("preferences", {}).get("preferred_difficulty", "未设置"),
    }


def _format_questions(questions: list[dict]) -> str:
    if not questions:
        return "（空）"
    out = []
    for i, q in enumerate(questions[:10], 1):
        text = str(q.get("question", "")).replace("\n", " ")[:200]
        diff = q.get("difficulty", "?")
        out.append(f"{i}. [难度{diff}] {text}")
    return "\n".join(out)


def _parse_score(content: str) -> float | None:
    """Extract {score: ...} from LLM output. Returns 1-10 float or None."""
    content = content.strip()
    # Strip ```...``` if present
    if "```" in content:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
        if m:
            content = m.group(1).strip()
    # Find first {
    for i, c in enumerate(content):
        if c == "{":
            try:
                data = json.loads(content[i:])
                s = data.get("score")
                if isinstance(s, (int, float)) and 0 < s <= 10:
                    return float(s)
            except json.JSONDecodeError:
                continue
            break
    # Fallback: regex for "score": N
    m = re.search(r'"score"\s*:\s*([0-9.]+)', content)
    if m:
        try:
            v = float(m.group(1))
            if 0 < v <= 10:
                return v
        except ValueError:
            pass
    return None


async def _ask_channel(channel: dict, prompt: str) -> tuple[float | None, str]:
    """Build a one-shot ChatOpenAI bound to this channel, invoke, parse score.

    Returns (score, reasoning). Either may be None / "" on failure.
    """
    try:
        # Build a minimal client without going through ResilientChatModel so
        # the channels don't share state (each must be queried independently).
        llm = ChatOpenAI(
            model=channel.get("model", ""),
            api_key=channel.get("api_key", ""),
            base_url=channel.get("api_base", ""),
            temperature=0.0,
            # Respect the channel's real output cap (or the tuning default); the old
            # hard 300 starved reasoning models — thinking tokens alone exceed it, so
            # the judge returned empty and every vote was discarded.
            max_tokens=int(channel.get("max_tokens") or get_tuning("max_output_tokens")),
            http_client=httpx.Client(timeout=httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=30.0)),
            http_async_client=httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=30.0)),
        )
        response = await llm.ainvoke([
            SystemMessage(content="你是面试题质量评审。只返回 JSON。"),
            HumanMessage(content=prompt),
        ])
        content = response.content if hasattr(response, "content") else str(response)
        score = _parse_score(content)
        # Reasoning may be in the JSON; cheap extract
        reason_match = re.search(r'"reasoning"\s*:\s*"([^"]{0,200})"', content)
        reasoning = reason_match.group(1) if reason_match else ""
        return score, reasoning
    except Exception as e:
        logger.warning("LLM judge channel %s failed: %s", channel.get("name", "?"), e)
        return None, ""


class LLMJudge:
    name = "llm_judge"

    async def evaluate(self, persona: dict, questions: list[dict]) -> tuple[float, str]:
        if not questions:
            return 0.0, "no questions"

        prompt = _JUDGE_PROMPT.format(
            **_format_persona_brief(persona),
            questions=_format_questions(questions),
        )

        channels = _select_distinct_channels(max_n=3)
        if not channels:
            # Single-channel config: fall back to one shot via default LLM
            from backend.llm_provider import get_langchain_llm
            llm = get_langchain_llm()
            try:
                response = await llm.ainvoke([
                    SystemMessage(content="你是面试题质量评审。只返回 JSON。"),
                    HumanMessage(content=prompt),
                ])
                content = response.content if hasattr(response, "content") else str(response)
                score = _parse_score(content)
                if score is None:
                    return 0.0, "single_channel_unparseable"
                return score / 10.0, f"single_channel score={score:.1f}/10"
            except Exception as e:
                logger.warning("LLM judge single-channel fallback failed: %s", e)
                return 0.0, "single_channel_failed"

        results = await asyncio.gather(
            *[_ask_channel(ch, prompt) for ch in channels],
            return_exceptions=True,
        )
        scores: list[float] = []
        notes: list[str] = []
        for ch, res in zip(channels, results):
            if isinstance(res, Exception):
                notes.append(f"{ch.get('model', '?')}=ERR")
                continue
            s, _ = res
            if s is None:
                notes.append(f"{ch.get('model', '?')}=NA")
                continue
            scores.append(s)
            notes.append(f"{ch.get('model', '?')}={s:.1f}")

        if not scores:
            return 0.0, "all_channels_failed"

        final = median(scores) / 10.0
        return final, "votes[" + ",".join(notes) + f"] median={median(scores):.1f}"
