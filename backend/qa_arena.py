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
from backend.context_assembler import resolve_input_budget, count_tokens, pack_messages
from backend.utils.sse_helpers import chunk_text as _chunk_text, chunk_reasoning as _chunk_reasoning

logger = logging.getLogger("uvicorn")

QA_ARENA_SYSTEM = """# 角色设定

你是一位有 10 年大厂经验的资深技术专家，写过百万行级生产代码，也面过几百号候选人。
你既懂技术深度，也懂"怎么把一件复杂事讲明白"。当前定位：用户的技术导师 + 模拟面试官。

## 你的风格特征

- **直接精准**：重要结论先说，再展开论证。不绕弯子，不堆砌
- **善用类比**：复杂概念用真实工程场景或生活类比讲清楚，但**类比只是辅助，专业术语不能被替换**
- **诚实的边界感**：不确定的细节（具体版本号、性能数字、API 签名）明确说"这个我记不太清，建议查文档/源码"，**绝不编造**
- **会温和纠错**：用户理解有偏差时直接指出"这里其实是 XX，不是 YY"，但不嘲讽

## 内部思考流程（**不要输出给用户**，只在脑内做）

回答前先在脑内判断三件事：
1. **问题类型**：事实查询 / 实现细节 / 系统设计 / 开放讨论？
2. **用户水平**：从提问的术语准确度推测（用对术语 → 中高级；混淆基本概念 → 初级）
3. **长度档位**：根据下面三档自适应

## 长度档位（最重要的约束）

### 档位 A — 简单事实/对比类（3-5 句）
触发：「XX 是什么」「XX 和 YY 区别」「XX 的默认值是多少」
示例：
> Q: Redis 和 Memcached 的区别？
> A: 三个核心差异：
> 1. **数据结构**：Redis 支持 string/list/hash/set/zset，Memcached 只有 KV
> 2. **持久化**：Redis 有 RDB/AOF，Memcached 纯内存重启即丢
> 3. **高可用**：Redis 自带 Sentinel/Cluster，Memcached 需要客户端分片
>
> 选型上：要复杂结构或持久化用 Redis；纯轻量缓存用 Memcached。

### 档位 B — 实现/原理类（≤200 字 + 必要时精简代码）
触发：「XX 怎么实现」「XX 的原理」「为什么 XX 设计成这样」
结构：分点说原理 → 一段精简代码或伪代码（如有必要）→ 一句话点出关键

### 档位 C — 设计/深度类（完整展开）
触发：「设计一个 XX 系统」「深入分析 XX」「如何优化 XX」
结构：**需求拆解 → 规模估算 → 方案选型 + 权衡 → 关键模块设计 → 潜在坑点**
代码只在能体现关键逻辑时给，不要为了凑长度而写。

**绝对禁止**：为了显得"有深度"把简单问题写成长篇。能 3 句说清的不用 5 句。

## 回复格式

1. 中文回复
2. 关键结论用 **加粗**，避免大段平铺文字
3. 代码块用 markdown 标注语言，注释用中文
4. 技术术语首次出现时给一句简短解释
5. 问题模糊时先用一句澄清再答（"你是想问 XX 还是 YY？"），不要预设错的方向回答
6. 不要在每次回复末尾加"总结一下"或"还有其他问题吗？"这类客套话"""

SUMMARY_SYSTEM = """你是一位忠实、全面的技术笔记整理专家。你的任务是把一段技术问答对话整理成一张高质量、可直接用于复习的知识卡片。

核心原则：
1. **忠实不编造**：只整理对话中真实讨论过的内容，绝不补充对话之外的知识；记不清/没讨论的不写。
2. **表格优先**：凡是横向对比、方案选型、概念辨析，一律用 Markdown 表格呈现，禁止降级成并列的 bullet 列表。
3. **保留原始表格与代码**：对话中已经出现的表格必须完整保留并补全表头/列；关键代码片段用带语言标注的代码块原样保留，不要改写成散文。
4. **全面覆盖**：对话讨论过的每一个知识点都要落到卡片里，不要只挑一两个；宁可多分几个小节，也不要漏。
5. **按内容裁剪小节**：给定的小节是"可选菜单"，对话没涉及的小节直接整节省略，绝不为凑结构写空话或占位句。"""

SUMMARY_USER_TEMPLATE = """请根据以下问答对话，生成一份结构化、可用于复习的知识卡片。

## 对话内容

{conversation}

## 输出要求

严格输出 Markdown（不要用代码块包裹整份输出）。按下面的小节组织，**对话未涉及的小节整节省略**：

# {{自动识别的主题名称}}
> {date} 问答演练总结

## 速览
（2-4 句话概括这次对话覆盖了哪些问题、得到的核心结论）

## 核心知识点
### 1. {{知识点名称}}
- **定义**: 用一两句话精确定义
- **原理 / 关键要点**: 列出关键要点，可展开多条（原理、步骤、适用场景等）
- **易错点**: 常见误解或容易混淆的地方

（对话讨论到的**每个**知识点都要列出，不要遗漏）

## 横向对比 / 选型
（对话中出现的任何 A vs B、多方案对比、概念辨析，**必须用 Markdown 表格还原**；
 若对话本身已用表格，保留并补全表头与列。没有对比内容则**省略本节**。）

| 维度 | 方案 A | 方案 B |
|------|--------|--------|
| ...  | ...    | ...    |

## 关键代码 / 实现要点
（保留对话中出现的核心代码片段，用带语言标注的代码块；没有代码则**省略本节**）

## 系统设计与权衡
（针对设计/架构类讨论：需求拆解 → 方案选型 → 权衡取舍 → 潜在坑点。没有则**省略本节**）

## 高频追问
- Q: {{对话中出现的或可延伸的面试常考问题}}?
- A: {{简洁回答}}

（列出 3-6 个）

## 待巩固 / 薄弱点
（基于对话中用户暴露的困惑或回答薄弱处，列出需要重点复习的点；没有明显薄弱点则**省略本节**）

注意：
1. 主题名称要准确反映对话讨论的核心内容
2. 所有内容均来自对话，不要编造对话中没有讨论的内容
3. 对比类内容用**表格**、代码类内容用**代码块**，不要降级成普通文字
4. 易错点/薄弱点要基于对话中用户的实际困惑或常见误区"""

MAP_PROMPT = """以下是一段较长技术问答对话的第 {idx}/{total} 段。请抽取本段中**真实讨论到**的内容，输出结构化的笔记片段，供后续汇总成知识卡片使用。

要求：
- 列出本段涉及的知识点（定义 / 关键要点 / 易错点）
- 本段出现的任何横向对比 / 选型 / 概念辨析，**用 Markdown 表格原样保留**；对话已有的表格必须完整保留，不要丢任何一张表
- 本段出现的关键代码，用带语言标注的代码块原样保留
- 本段出现的高频追问（Q/A）
- 只整理本段真实出现的内容，不编造、不补充对话外知识
- 直接输出片段内容，不要写"本段总结如下"之类的开场白

## 本段对话

{conversation}"""


async def _build_memory_context(user_message: str, user_id: str) -> str:
    """Retrieve long-term vector memory relevant to the current question."""
    try:
        from backend.vector_memory import search_memory
        results = await search_memory(user_message, user_id, top_k=5)
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


# Per-message caps when feeding the conversation to the summarizer.
# User turns are short questions; AI turns carry the substance (tables/code/design),
# so they get a much larger budget. The old flat 1000-char cut destroyed long answers
# and sliced Markdown tables mid-row.
QA_USER_CAP = 2000
QA_ASSISTANT_CAP = 16000


def _truncate_on_boundary(text: str, cap: int) -> str:
    """Truncate to <= cap chars without cutting through a line (table row / code line).

    Snaps back to the last newline before the cap so we never emit a half-row of a
    Markdown table. Rarely fires given the generous caps above.
    """
    if len(text) <= cap:
        return text
    head = text[:cap]
    nl = head.rfind("\n")
    if nl > cap * 0.6:  # only snap back if it doesn't discard too much
        head = head[:nl]
    return head.rstrip() + "\n\n…(本段过长，已节选)"


def _format_message(m: dict, user_cap: int = QA_USER_CAP, assistant_cap: int = QA_ASSISTANT_CAP) -> str:
    role = "用户" if m["role"] == "user" else "AI"
    cap = user_cap if m["role"] == "user" else assistant_cap
    return f"[{role}] {_truncate_on_boundary(m['content'], cap)}"


def _format_conversation(
    messages: list[dict], user_cap: int = QA_USER_CAP, assistant_cap: int = QA_ASSISTANT_CAP
) -> str:
    return "\n\n".join(_format_message(m, user_cap, assistant_cap) for m in messages)


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
MAX_RESPONSE_STORE_LENGTH = 16000

# TEMP DIAGNOSTIC: log the raw shape of the first few streamed chunks so we can see what
# the gateway actually sends (does reasoning arrive? under which additional_kwargs key?).
# Capped across the process; remove once the reasoning channel is understood.
_DIAG_CHUNKS = {"n": 0, "limit": 12}

COMPRESS_PROMPT = (
    "请将以下对话内容压缩为一段简要摘要（200字以内），保留关键技术概念、"
    "用户的理解程度和讨论结论。只输出摘要，不要其他内容。\n\n{conversation}"
)

# Incremental variant: fold NEW turns into the existing rolling summary instead
# of re-summarizing the whole history from scratch. Cost is O(new turns) and old
# context survives in compressed form. Output stays bounded (≤ ~600 chars).
INCREMENTAL_COMPRESS_PROMPT = (
    "下面是一段技术对话的【已有摘要】，以及在它之后【新增的对话】。"
    "请把新增对话中的关键技术概念、用户理解程度和讨论结论，融合进已有摘要，"
    "输出一段更新后的完整摘要（300字以内）。保留已有摘要中仍然重要的信息，"
    "去掉冗余。只输出更新后的摘要，不要其他内容。\n\n"
    "## 已有摘要\n{prev_summary}\n\n## 新增的对话\n{conversation}"
)


async def _get_or_create_summary(
    session_id: str, user_id: str, history: list[dict], total_count: int,
) -> tuple[str, int]:
    """Return ``(rolling_summary, covered)`` where ``covered`` is how many of the
    OLDEST messages are folded into the summary.

    Incremental: reuses the cached summary and only folds in the turns added
    since it was last built (prev_summary + new turns → updated summary), instead
    of re-summarizing the whole history from scratch each time. Cost is O(new
    turns); older context is preserved in compressed form rather than re-read. The
    raw tail the caller still sends verbatim is ``history[covered:]``.
    """
    target_cover = total_count - KEEP_RECENT
    if target_cover <= 0:
        return "", 0

    cached = store.get_context_summary(session_id, user_id)
    prev_summary, covered = "", 0
    if cached:
        prev_summary, covered = cached
        # Legacy rows stored len(history)-at-summary-time, not a cover count; if
        # that exceeds the cover target the semantics don't line up — rebuild.
        if covered > target_cover:
            prev_summary, covered = "", 0
        elif target_cover - covered < SUMMARY_REGEN_INTERVAL:
            return prev_summary, covered  # fresh enough; reuse as-is

    new_msgs = history[covered:target_cover]
    if not new_msgs:
        return prev_summary, covered

    # Modest per-message caps keep the incremental call cheap (target ~300 chars).
    conversation = _format_conversation(new_msgs, user_cap=800, assistant_cap=1500)
    llm = get_langchain_llm()
    try:
        if prev_summary:
            user_content = INCREMENTAL_COMPRESS_PROMPT.format(
                prev_summary=prev_summary, conversation=conversation,
            )
        else:
            user_content = COMPRESS_PROMPT.format(conversation=conversation)
        resp = await llm.ainvoke([
            {"role": "system", "content": "你是对话摘要助手。"},
            {"role": "user", "content": user_content},
        ])
        summary = (resp.content or "").strip()[:600]
    except Exception as e:
        logger.warning("Context compression failed: %s", e)
        # Keep the prior summary if we have one; else a raw excerpt of the new turns.
        summary = prev_summary or conversation[:300]

    store.save_context_summary(session_id, user_id, summary, target_cover)
    return summary, target_cover


def _sse(payload: dict) -> str:
    """Serialize one SSE ``data:`` frame (ensure_ascii=False + trailing blank line)."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream_chat_answer(
    session_id: str, user_id: str, history: list[dict], prompt: str
) -> AsyncGenerator[str, None]:
    """Core QA streaming, shared by first-send and regenerate.

    ``history`` is the prior conversation (NOT including ``prompt``); ``prompt`` is the
    current user question to answer. Persists the assistant reply itself so callers stay
    thin. Three mutually-exclusive terminal states:
      - non-empty content, clean finish     → save assistant + ``done``
      - non-empty content, mid-stream error → save partial  + ``error`` (no ``done``)
      - empty content (any reason)          → persist nothing + ``error``

    Emits ``stage`` events (memory → thinking → answering) and ``reasoning`` deltas so the
    front end can show progress and the SSE stream never goes byte-silent during a long
    reasoning phase (which a proxy would otherwise time out → blank reply).
    """
    # Stage 1 — memory retrieval. Emit the marker first so the UI shows progress while the
    # (possibly slow) vector search runs, instead of a frozen spinner.
    yield _sse({"type": "stage", "stage": "memory", "message": "正在检索相关记忆…"})
    memory_ctx = await _build_memory_context(prompt, user_id)

    # Keep QA_ARENA_SYSTEM as a STABLE prefix so the channel's automatic prompt-prefix
    # cache can hit across turns. Dynamic content (retrieved memory, rolling summary)
    # must NOT be concatenated onto the system message — that would change the prefix
    # every turn and defeat caching. They go in as separate messages after the prefix.
    lc_messages: list[dict] = [{"role": "system", "content": QA_ARENA_SYSTEM}]

    # Context compression for long conversations. The rolling summary is built
    # incrementally; the raw tail we still send verbatim is everything AFTER the
    # part the summary already covers (not a fixed last-N), so the middle is never
    # lost between the summary and the recent window.
    if len(history) > COMPRESSION_THRESHOLD:
        summary, covered = await _get_or_create_summary(session_id, user_id, history, len(history))
        raw_tail = history[covered:]
        summary_block = (
            f"## 之前的对话摘要\n{summary}\n\n（以下是最近的对话记录）" if summary else ""
        )
    else:
        summary_block = ""
        raw_tail = history

    # #4 token budgeting: the system prefix, rolling summary, retrieved memory and
    # the user prompt are all bounded and kept whole; budget only the raw tail
    # (the part that grows) so a very long recent window can't overflow the input
    # window. pack_messages drops the OLDEST tail turns first, always keeping the
    # latest two.
    budget = resolve_input_budget()
    reserved = (
        count_tokens(QA_ARENA_SYSTEM) + count_tokens(prompt)
        + count_tokens(summary_block) + count_tokens(memory_ctx)
    )
    hist_budget = max(1000, budget - reserved)
    kept_tail, _tail_report = pack_messages(raw_tail, hist_budget, keep_last=2)

    if summary_block:
        lc_messages.append({"role": "system", "content": summary_block})
    for m in kept_tail:
        lc_messages.append({"role": m["role"], "content": m["content"]})

    if memory_ctx:
        lc_messages.append({"role": "system", "content": memory_ctx})

    lc_messages.append({"role": "user", "content": prompt})

    # Stage 2 — model call. A reasoning model can "think" for a long time before any visible
    # answer token; we stream that thinking (see _chunk_reasoning) to keep the SSE connection
    # alive past proxy idle timeouts AND to show the user progress.
    yield _sse({"type": "stage", "stage": "thinking", "message": "正在思考…"})

    content = ""
    had_reasoning = False
    answering = False
    stream_error = False
    try:
        llm = get_langchain_llm()
        aiter = llm.astream(lc_messages).__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(aiter.__anext__(), timeout=IDLE_HEARTBEAT_SECONDS)
                if _DIAG_CHUNKS["n"] < _DIAG_CHUNKS["limit"]:
                    _DIAG_CHUNKS["n"] += 1
                    _c = getattr(chunk, "content", None)
                    logger.warning(
                        "QA chunk diag #%d: content_type=%s content=%.60r akw_keys=%s meta_keys=%s",
                        _DIAG_CHUNKS["n"], type(_c).__name__, _c,
                        list(getattr(chunk, "additional_kwargs", {}) or {}),
                        list(getattr(chunk, "response_metadata", {}) or {}),
                    )
                reasoning = _chunk_reasoning(chunk)
                if reasoning:
                    had_reasoning = True
                    yield _sse({"type": "reasoning", "content": reasoning})
                token = _chunk_text(chunk)
                if token:
                    if not answering:
                        answering = True
                        yield _sse({"type": "stage", "stage": "answering", "message": "正在作答…"})
                    content += token
                    yield _sse({"type": "token", "content": token})
            except asyncio.TimeoutError:
                # No chunk at all for IDLE_HEARTBEAT_SECONDS — upstream is genuinely silent
                # (deep reasoning with no streamed trace, or a stalled relay). Ping to keep
                # the proxy from closing the idle connection.
                yield _sse({"type": "ping"})
            except StopAsyncIteration:
                break
    except Exception as e:
        # astream can't fail over once the first chunk is out, so a mid-stream drop
        # propagates here. Whatever streamed before the drop is preserved below.
        logger.error("QA arena LLM call failed: %s", e)
        stream_error = True

    content = content.strip()

    # Empty completion — channel cooldown/failover, content filter, a reasoning model
    # that produced no visible answer, or a provider that streamed content in a field we
    # don't read. The backend call "succeeded" yet there is nothing to show. Don't persist
    # a blank turn or send a silent ``done`` (which renders an empty bubble): surface a
    # retry so the user can regenerate — a fresh attempt / channel often succeeds. Mirrors
    # the guard already on the summary path.
    if not content:
        logger.warning(
            "QA arena produced empty content for session %s (had_reasoning=%s, stream_error=%s)",
            session_id, had_reasoning, stream_error,
        )
        msg = (
            "模型只输出了思考过程、没有给出正文，请点击重新生成"
            if had_reasoning
            else "模型暂时无响应，请点击重新生成"
        )
        yield _sse({"type": "error", "message": msg})
        return

    # Persist whatever we got (full or partial) so a reload shows it and regenerate can
    # cleanly replace it.
    store.save_message(session_id, user_id, "assistant", content[:MAX_RESPONSE_STORE_LENGTH])

    if stream_error:
        yield _sse({"type": "error", "message": "回复被中断，可能不完整，请点击重新生成"})
    else:
        yield _sse({"type": "done"})

    # Lightweight profile evolution: track QA activity for learning insights.
    # Runs after the stream so it never blocks the response.
    try:
        from backend.memory import update_profile_realtime
        update_profile_realtime(
            mode="qa_arena",
            topic=None,
            user_id=user_id,
            score_entry={"score": None, "question": prompt[:80]},
        )
    except Exception:
        pass  # Non-critical, don't break the chat flow


async def stream_qa_chat(
    session_id: str, message: str, user_id: str
) -> AsyncGenerator[str, None]:
    """Stream SSE events for a new QA arena chat turn (saves the user message)."""
    # Full history: the rolling-summary covered cursor indexes from message 0, and
    # the compression + token-budget layers below already bound what reaches the LLM.
    history = store.load_messages(session_id, user_id, limit=None)
    store.save_message(session_id, user_id, "user", message)

    # Auto-title on first user message
    if not history:
        title = message[:20].strip()
        if len(message) > 20:
            title += "..."
        store.update_session_title(session_id, user_id, title)

    async for event in _stream_chat_answer(session_id, user_id, history, message):
        yield event


async def stream_qa_regenerate(
    session_id: str, user_id: str
) -> AsyncGenerator[str, None]:
    """Re-answer the last user question, replacing any prior broken/partial/empty reply.

    The user message is NOT re-saved; the trailing assistant message (if any) is dropped
    so the regenerated answer cleanly replaces it. Shares the same context window as
    ``stream_qa_chat`` so the regenerated answer sees the same history.
    """
    store.delete_last_message_if_assistant(session_id, user_id)
    messages = store.load_messages(session_id, user_id, limit=None)
    if not messages or messages[-1]["role"] != "user":
        yield f"data: {json.dumps({'type': 'error', 'message': '没有可重新生成的提问'}, ensure_ascii=False)}\n\n"
        return
    prompt = messages[-1]["content"]
    history = messages[:-1]
    async for event in _stream_chat_answer(session_id, user_id, history, prompt):
        yield event


SINGLE_PASS_BUDGET = 40000  # formatted-conversation chars that still fit one summary call
CHUNK_SIZE = 12000          # per-chunk size for the map phase on longer conversations


def _chunk_conversation(messages: list[dict], chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Split the formatted conversation into chunks on message boundaries (never mid-message)."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for m in messages:
        block = _format_message(m)
        if current and current_len + len(block) > chunk_size:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(block)
        current_len += len(block)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


async def _map_chunk_to_notes(chunk: str, idx: int, total: int, reasoning_effort: str | None = None) -> str:
    """Map phase: summarize one conversation chunk into structured note fragments.

    On failure, keep a raw excerpt of the chunk rather than dropping it, so no part of
    the session is silently lost.
    """
    llm = get_langchain_llm(reasoning_effort=reasoning_effort)
    prompt = MAP_PROMPT.format(idx=idx, total=total, conversation=chunk)
    try:
        resp = await llm.ainvoke([
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": prompt},
        ])
        text = (resp.content or "").strip()
        if text:
            return text
    except Exception as e:
        logger.warning("Map-phase summary failed for chunk %d/%d: %s", idx, total, e)
    return f"（第 {idx} 段自动整理失败，保留原文节选）\n{chunk[:4000]}"


async def stream_generate_summary(
    session_id: str, user_id: str, reasoning_effort: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream SSE events for knowledge card generation.

    reasoning_effort: per-call override (minimal/low/medium/high). Summary is an
    extract-and-reorganize task, not deep reasoning, so a lower effort cuts latency
    sharply with little quality loss. None/"" keeps the configured default.
    """
    # Full history — the map-reduce path below exists precisely so long sessions
    # are summarized in full instead of being truncated.
    messages = store.load_messages(session_id, user_id, limit=None)
    if len(messages) < 2:
        yield f"data: {json.dumps({'type': 'error', 'message': '对话内容太少，无法生成总结'}, ensure_ascii=False)}\n\n"
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yield f"data: {json.dumps({'type': 'progress', 'message': '正在分析对话内容...'}, ensure_ascii=False)}\n\n"

    conversation = _format_conversation(messages)

    # Long conversations: map-reduce so no part of the session is silently dropped.
    if len(conversation) > SINGLE_PASS_BUDGET:
        chunks = _chunk_conversation(messages)
        notes: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            yield f"data: {json.dumps({'type': 'progress', 'message': f'正在整理第 {i}/{len(chunks)} 段...'}, ensure_ascii=False)}\n\n"
            notes.append(await _map_chunk_to_notes(chunk, i, len(chunks), reasoning_effort=reasoning_effort))
        conversation = "\n\n---\n\n".join(notes)
        yield f"data: {json.dumps({'type': 'progress', 'message': '正在汇总知识卡片...'}, ensure_ascii=False)}\n\n"

    prompt_text = SUMMARY_USER_TEMPLATE.format(conversation=conversation, date=today)

    content = ""
    try:
        llm = get_langchain_llm(reasoning_effort=reasoning_effort)
        aiter = llm.astream([
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": prompt_text},
        ]).__aiter__()
        chars_since_heartbeat = 0
        while True:
            try:
                chunk = await asyncio.wait_for(aiter.__anext__(), timeout=IDLE_HEARTBEAT_SECONDS)
                token = _chunk_text(chunk)
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

    # Model occasionally returns an empty completion (transient channel cooldown/failover)
    # without raising — don't persist a blank card or send an empty "complete"; surface a retry.
    if not content:
        logger.warning("Summary generation produced empty content for session %s", session_id)
        yield f"data: {json.dumps({'type': 'error', 'message': '生成结果为空，请重试（模型可能临时无响应）'}, ensure_ascii=False)}\n\n"
        return

    topic = _extract_topic(content)
    safe_topic = _sanitize_filename(topic)
    # Session id in the filename so get_summary_file can resolve THIS session's
    # card instead of whichever card was written most recently.
    filename = f"{today}-{safe_topic}-{session_id}.md"

    notes_dir = settings.base_dir / "data" / "qa_notes" / user_id
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / filename).write_text(content, encoding="utf-8")

    try:
        from backend.embedding_tasks import schedule_session_memory_index
        schedule_session_memory_index(
            session_id=session_id, topic=topic, summary=content[:4000],
            weak_points=[], user_id=user_id, insight_text=content[:4000],
        )
    except Exception as e:
        logger.warning("Failed to schedule QA summary indexing: %s", e)

    result = {"content": content, "filename": filename, "topic": topic}
    yield f"data: {json.dumps({'type': 'complete', 'data': result}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


def get_summary_file(session_id: str, user_id: str) -> tuple[str, str] | None:
    """Find the most recent summary file for a session. Returns (content, filename) or None."""
    notes_dir = settings.base_dir / "data" / "qa_notes" / user_id
    if not notes_dir.exists():
        return None
    # Prefer files tagged with this session id (see stream_generate_summary).
    # Legacy cards predate the tag, so fall back to the user's most recent file
    # — but only when the session has NO tagged card, to avoid handing back a
    # different session's summary.
    files = sorted(
        notes_dir.glob(f"*-{session_id}.md"), key=lambda f: f.stat().st_mtime, reverse=True
    ) or sorted(notes_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return None
    f = files[0]
    return f.read_text(encoding="utf-8"), f.name
