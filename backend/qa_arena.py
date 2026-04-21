"""问答演练场 — 自由问答 + 长期记忆 + 背诵卡片式总结导出。"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from backend.config import settings
from backend.llm_provider import get_langchain_llm
from backend.storage import qa_sessions as store

logger = logging.getLogger("uvicorn")

QA_ARENA_SYSTEM = """# 角色设定

你是一位专业的技术导师，专注于帮助用户深入理解技术概念。

## 你的风格
- 回答准确、有深度，适当给出代码示例
- 善于用类比和分层讲解将复杂概念讲清楚
- 当用户理解有误时，温和纠正并解释正确概念
- 主动追问以确认用户真正理解（"你觉得 X 和 Y 的关键区别是什么？"）
- 鼓励用户思考，而不是直接给完整答案

## 回复原则
1. 使用中文回复
2. 技术术语首次出现时提供简短解释
3. 回答要有结构（分点、分层），避免一大段文字
4. 代码示例用 markdown 代码块，注释用中文
5. 当问题模糊时，先澄清再回答
6. 适时总结已讨论的要点"""

SUMMARY_SYSTEM = "你是一位技术知识整理专家。请根据对话内容，提取和整理关键知识点，生成结构化的学习笔记。"

SUMMARY_USER_TEMPLATE = """请根据以下问答对话，生成一份结构化的知识总结卡片。

## 对话内容

{conversation}

## 输出要求

请严格按照以下 Markdown 格式输出（不要用代码块包裹整个输出）：

# {{自动识别的主题名称}}
> {date} 问答演练总结

## 核心知识点
### 1. {{知识点名称}}
- **定义**: 用一两句话精确定义
- **关键要点**: 列出 2-4 个关键要点
- **易错点**: 常见误解或容易混淆的地方

### 2. {{知识点名称}}
- **定义**: ...
- **关键要点**: ...
- **易错点**: ...

（根据对话实际内容，列出所有讨论到的知识点）

## 高频追问
- Q: {{对话中出现的或可能被追问的问题}}?
- A: {{简洁的回答}}

（列出 3-5 个高频追问）

注意：
1. 主题名称要准确反映对话讨论的核心内容
2. 知识点要从对话中提取，不要编造对话中没有讨论的内容
3. 易错点要基于对话中用户的实际困惑或常见误区
4. 高频追问可以包含对话中出现的以及延伸的面试常考问题"""


def _build_memory_context(user_message: str, user_id: str) -> str:
    """Retrieve long-term vector memory relevant to the current question."""
    try:
        from backend.vector_memory import search_memory
        results = search_memory(user_message, user_id, top_k=5)
    except Exception as e:
        logger.warning("Vector memory search failed (embedding may not be configured): %s", e)
        return ""
    if not results:
        return ""
    lines = []
    for r in results:
        lines.append(f"- [{r.get('topic', '未知')}] {r['content'][:200]}")
    return (
        "\n\n## 相关历史知识（来自你之前的学习记录）\n\n"
        + "\n".join(lines)
        + "\n\n如果上述知识与当前问题相关，可以自然融入回答中，但不要强行提及。"
    )


def _format_conversation(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = "用户" if m["role"] == "user" else "AI"
        content = m["content"][:1000]
        lines.append(f"[{role}] {content}")
    return "\n\n".join(lines)


def _sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\u4e00-\u9fff\-]", "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name[:50]


def _extract_topic(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# ") and len(line) > 2:
            return line[2:].strip()
    return "技术问答总结"


COMPRESSION_THRESHOLD = 20
KEEP_RECENT = 10
SUMMARY_REGEN_INTERVAL = 10
IDLE_HEARTBEAT_SECONDS = 30
MAX_RESPONSE_STORE_LENGTH = 8000

COMPRESS_PROMPT = (
    "请将以下对话内容压缩为一段简要摘要（200字以内），保留关键技术概念、"
    "用户的理解程度和讨论结论。只输出摘要，不要其他内容。\n\n{conversation}"
)


async def _get_or_create_summary(
    session_id: str, user_id: str, old_messages: list[dict], total_count: int,
) -> str:
    """Return cached summary or generate a new one."""
    cached = store.get_context_summary(session_id, user_id)
    if cached:
        summary_text, summary_count = cached
        if total_count - summary_count < SUMMARY_REGEN_INTERVAL:
            return summary_text

    conversation = _format_conversation(old_messages)
    llm = get_langchain_llm()
    try:
        resp = await llm.ainvoke([
            {"role": "system", "content": "你是对话摘要助手。"},
            {"role": "user", "content": COMPRESS_PROMPT.format(conversation=conversation)},
        ])
        summary = (resp.content or "").strip()[:500]
    except Exception as e:
        logger.warning("Context compression failed: %s", e)
        summary = conversation[:300]

    store.save_context_summary(session_id, user_id, summary, total_count)
    return summary


async def stream_qa_chat(
    session_id: str, message: str, user_id: str
) -> AsyncGenerator[str, None]:
    """Stream SSE events for a QA arena chat turn."""
    history = store.load_messages(session_id, user_id, limit=50)
    store.save_message(session_id, user_id, "user", message)

    # Auto-title on first user message
    if not history:
        title = message[:20].strip()
        if len(message) > 20:
            title += "..."
        store.update_session_title(session_id, user_id, title)

    memory_ctx = _build_memory_context(message, user_id)
    system_prompt = QA_ARENA_SYSTEM + memory_ctx

    # Context compression for long conversations
    if len(history) > COMPRESSION_THRESHOLD:
        old_messages = history[:-KEEP_RECENT]
        recent_messages = history[-KEEP_RECENT:]
        summary = await _get_or_create_summary(session_id, user_id, old_messages, len(history))
        system_prompt += f"\n\n## 之前的对话摘要\n{summary}\n\n（以下是最近的对话记录）"
        lc_messages = [{"role": "system", "content": system_prompt}]
        for m in recent_messages:
            lc_messages.append({"role": m["role"], "content": m["content"]})
    else:
        lc_messages = [{"role": "system", "content": system_prompt}]
        for m in history:
            lc_messages.append({"role": m["role"], "content": m["content"]})

    lc_messages.append({"role": "user", "content": message})

    content = ""
    try:
        llm = get_langchain_llm()
        aiter = llm.astream(lc_messages).__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(aiter.__anext__(), timeout=IDLE_HEARTBEAT_SECONDS)
                token = chunk.content if hasattr(chunk, "content") else ""
                if token:
                    content += token
                    yield f"data: {json.dumps({'type': 'token', 'content': token}, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
            except StopAsyncIteration:
                break
    except Exception as e:
        logger.error("QA arena LLM call failed: %s", e)
        if not content:
            content = "抱歉，AI 服务暂时不可用，请稍后重试。"
            yield f"data: {json.dumps({'type': 'token', 'content': content}, ensure_ascii=False)}\n\n"

    yield f"data: {json.dumps({'type': 'done'})}\n\n"

    store.save_message(session_id, user_id, "assistant", content[:MAX_RESPONSE_STORE_LENGTH])


MAX_SUMMARY_CONVERSATION_LENGTH = 15000


async def stream_generate_summary(
    session_id: str, user_id: str,
) -> AsyncGenerator[str, None]:
    """Stream SSE events for knowledge card generation."""
    messages = store.load_messages(session_id, user_id, limit=200)
    if len(messages) < 2:
        yield f"data: {json.dumps({'type': 'error', 'message': '对话内容太少，无法生成总结'}, ensure_ascii=False)}\n\n"
        return

    conversation = _format_conversation(messages)
    if len(conversation) > MAX_SUMMARY_CONVERSATION_LENGTH:
        conversation = conversation[:MAX_SUMMARY_CONVERSATION_LENGTH] + "\n\n...(对话过长，已截断)"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt_text = SUMMARY_USER_TEMPLATE.format(conversation=conversation, date=today)

    yield f"data: {json.dumps({'type': 'progress', 'message': '正在分析对话内容...'}, ensure_ascii=False)}\n\n"

    content = ""
    try:
        llm = get_langchain_llm()
        aiter = llm.astream([
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": prompt_text},
        ]).__aiter__()
        chars_since_heartbeat = 0
        while True:
            try:
                chunk = await asyncio.wait_for(aiter.__anext__(), timeout=IDLE_HEARTBEAT_SECONDS)
                token = chunk.content if hasattr(chunk, "content") else ""
                if token:
                    content += token
                    chars_since_heartbeat += len(token)
                    if chars_since_heartbeat >= 200:
                        yield f"data: {json.dumps({'type': 'progress', 'message': f'正在生成知识卡片... ({len(content)} 字)'}, ensure_ascii=False)}\n\n"
                        chars_since_heartbeat = 0
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
            except StopAsyncIteration:
                break
    except Exception as e:
        logger.error("Summary generation failed: %s", e)
        yield f"data: {json.dumps({'type': 'error', 'message': '生成失败，请稍后重试'}, ensure_ascii=False)}\n\n"
        return

    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```\s*$", "", content)

    topic = _extract_topic(content)
    safe_topic = _sanitize_filename(topic)
    filename = f"{today}-{safe_topic}.md"

    notes_dir = settings.base_dir / "data" / "qa_notes" / user_id
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / filename).write_text(content, encoding="utf-8")

    try:
        from backend.vector_memory import index_session_memory
        index_session_memory(
            session_id=session_id, topic=topic, summary=content[:2000],
            weak_points=[], user_id=user_id, insight_text=content[:2000],
        )
    except Exception as e:
        logger.warning("Failed to index QA summary into vector memory: %s", e)

    result = {"content": content, "filename": filename, "topic": topic}
    yield f"data: {json.dumps({'type': 'complete', 'data': result}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


def get_summary_file(session_id: str, user_id: str) -> tuple[str, str] | None:
    """Find the most recent summary file for a session. Returns (content, filename) or None."""
    notes_dir = settings.base_dir / "data" / "qa_notes" / user_id
    if not notes_dir.exists():
        return None
    files = sorted(notes_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return None
    f = files[0]
    return f.read_text(encoding="utf-8"), f.name
