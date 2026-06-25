"""Knowledge evolution — auto-writeback from interviews + high-freq collection."""
import asyncio
import logging
from pathlib import Path

from backend.config import settings
from backend.llm_provider import get_langchain_llm
from backend.utils.files import atomic_write_text

logger = logging.getLogger("uvicorn")

# Serializes all read-modify-write append operations on knowledge / high-freq
# files. These run as fire-and-forget background coroutines (drill writeback, QA
# ingest, high-freq collection) that may overlap; without this, two "read full
# file → append → write back" cycles could interleave and silently drop one
# side's append (lost update). Combined with atomic_write_text, every append is
# both crash-safe and race-safe. Writes are infrequent, so one global lock is fine.
_writeback_lock = asyncio.Lock()

# Per-deposit size cap. Auto-extracted knowledge / cleaned QA cards are short by
# nature; capping each appended block keeps the auto-deposit files from growing
# without bound (which would slow every retrieval and rebuild on that topic).
_MAX_DEPOSIT_CHARS = 8000


def _cap_deposit(text: str) -> str:
    """Trim a single deposit block to the per-deposit cap, on a line boundary."""
    if len(text) <= _MAX_DEPOSIT_CHARS:
        return text
    head = text[:_MAX_DEPOSIT_CHARS]
    nl = head.rfind("\n")
    if nl > _MAX_DEPOSIT_CHARS * 0.6:
        head = head[:nl]
    return head.rstrip() + "\n\n<!-- 已截断（超单次沉淀上限） -->"


def _append_sync(path: Path, block: str) -> None:
    """Read-modify-write append, atomic. Runs in a worker thread."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    atomic_write_text(path, existing + block)


async def _append_atomic(path: Path, block: str) -> None:
    """Append ``block`` to ``path`` race-safe (lock) + crash-safe (atomic) + off-loop."""
    async with _writeback_lock:
        await asyncio.to_thread(_append_sync, path, block)

_EXTRACT_PROMPT = """你是一个知识提取引擎。请从以下面试 Q&A 中提取有价值的知识点。

## Q&A 列表
{qa_text}

## 任务
- 对于得分 >= 7 的回答，提取其中体现的深度知识、最佳实践或独到见解
- 对于得分 < 6 的回答，提取正确答案方向和关键概念作为参考

每个知识点用 `## ` 开头，简洁明确，包含核心概念和实际应用。
只返回 Markdown 知识点，不要其他内容。"""


async def extract_and_writeback(
    topic: str, questions: list, answers: list, scores: list, user_id: str
):
    """Extract knowledge from Q&A and append to topic knowledge base."""
    try:
        worthy = []
        for i, s in enumerate(scores):
            if i >= len(questions):
                break
            score_val = s.get("score", 5) if isinstance(s, dict) else 5
            if score_val >= 7 or score_val < 6:
                q_text = questions[i].get("question", str(questions[i])) if isinstance(questions[i], dict) else str(questions[i])
                a_text = answers[i] if i < len(answers) else "(未作答)"
                assessment = s.get("assessment", "") if isinstance(s, dict) else ""
                worthy.append(
                    f"Q: {q_text}\nA: {a_text}\n得分: {score_val}\n评价: {assessment}"
                )

        if not worthy:
            return

        qa_text = "\n\n---\n\n".join(worthy)
        llm = get_langchain_llm()
        from langchain_core.messages import HumanMessage
        response = await llm.ainvoke([HumanMessage(content=_EXTRACT_PROMPT.format(qa_text=qa_text))])
        extracted = response.content.strip()
        if not extracted or len(extracted) < 20:
            return

        topics = _get_topic_dir(topic, user_id)
        if not topics:
            return

        target = topics / "自动沉淀.md"
        from datetime import datetime
        header = f"\n\n---\n\n<!-- 自动沉淀 {datetime.now().strftime('%Y-%m-%d %H:%M')} -->\n\n"
        extracted = _cap_deposit(extracted)
        await _append_atomic(target, header + extracted + "\n")

        # Background embedding: only embed the new knowledge content (incremental insert).
        # No full rebuild — the new .md content is the only thing that changed,
        # and incremental_insert_to_index handles it efficiently.
        from backend.embedding_tasks import schedule_incremental_insert
        schedule_incremental_insert(topic, user_id, extracted)
        logger.info(f"Knowledge writeback: {len(worthy)} items → {target}")
    except Exception as e:
        logger.warning(f"Knowledge writeback failed for {topic}: {e}")


async def collect_high_freq(
    topic: str, questions: list, scores: list, user_id: str
):
    """Collect low-scoring questions into high-freq bank for future review."""
    try:
        low_score_items = []
        for i, s in enumerate(scores):
            if i >= len(questions):
                break
            score_val = s.get("score", 5) if isinstance(s, dict) else 5
            if score_val < 6:
                q_text = questions[i].get("question", str(questions[i])) if isinstance(questions[i], dict) else str(questions[i])
                assessment = s.get("assessment", "") if isinstance(s, dict) else ""
                low_score_items.append((q_text, score_val, assessment))

        if not low_score_items:
            return

        high_freq_dir = settings.user_high_freq_path(user_id)
        filepath = high_freq_dir / f"{topic}.md"

        from datetime import datetime
        lines = [f"\n\n<!-- {datetime.now().strftime('%Y-%m-%d %H:%M')} -->"]
        for q, score, assessment in low_score_items:
            lines.append(f"\n## Q: {q}\n得分: {score}\n评估: {assessment}\n---")

        await _append_atomic(filepath, "\n".join(lines) + "\n")
        logger.info(f"High-freq collection: {len(low_score_items)} items → {filepath}")
    except Exception as e:
        logger.warning(f"High-freq collection failed for {topic}: {e}")


def _get_topic_dir(topic: str, user_id: str) -> Path | None:
    """Get the knowledge directory for a topic."""
    from backend.indexer import get_topic_map
    topic_map = get_topic_map(user_id)
    if topic not in topic_map:
        return None
    return settings.user_knowledge_path(user_id) / topic_map[topic]


def _resolve_topic_key(raw: str, topics: dict) -> str:
    """Map an LLM topic answer to a real topic key, tolerant of case / name / extra text.

    The classifier is told to return a key, but models often return the display name,
    a different case, or a short phrase. Resolution order: exact key/name token
    (case-insensitive) → display name contained in the answer → a sufficiently long key
    contained in the answer (len ≥ 4, so a short key like 'go' can't match 'good').
    Returns "" for no match or an explicit NONE.
    """
    if not raw:
        return ""
    raw = raw.strip()
    if raw.lower() == "none":
        return ""
    lower_key = {k.lower(): k for k in topics}
    lower_name = {str(v.get("name", "")).strip().lower(): k for k, v in topics.items() if v.get("name")}
    tokens = [raw] + [t.strip("`\"' .：:，,。、") for t in raw.replace("\n", " ").split()]
    for t in tokens:
        tl = t.lower()
        if tl in lower_key:
            return lower_key[tl]
        if tl in lower_name:
            return lower_name[tl]
    low = raw.lower()
    for nl, k in lower_name.items():
        if nl and nl in low:
            return k
    for kl, k in lower_key.items():
        if len(kl) >= 4 and kl in low:
            return k
    return ""


async def ingest_qa_card_to_knowledge(card_content: str, user_id: str) -> dict:
    """Promote a QA-arena knowledge card into the RAG knowledge base (user-confirmed).

    Unlike drill writeback (score-gated), a free-chat summary has no quality signal, so we
    gate on (1) explicit user action — the caller is a button — and (2) an LLM "factualize"
    pass that drops uncertain/personal/chat-bound content. The result lands in a separate,
    provenance-marked file (用户沉淀_qa.md) under the classified topic, then is incrementally
    embedded so future RAG retrieval can use it. Returns {ok, topic, reason}.
    """
    from langchain_core.messages import HumanMessage
    from backend.indexer import load_topics, get_topic_map
    from backend.prompts.knowledge import QA_TOPIC_CLASSIFY_PROMPT, QA_KNOWLEDGE_CLEAN_PROMPT

    card_content = (card_content or "").strip()
    if len(card_content) < 20:
        return {"ok": False, "topic": None, "reason": "卡片内容过少，未收录"}

    topics = load_topics(user_id)
    if not topics:
        return {"ok": False, "topic": None, "reason": "暂无可用知识库主题"}

    # 1) classify into ONE existing topic key (or NONE). Low reasoning — trivial routing;
    #    high effort would only add latency (and a long "thinking" wait on the button).
    #    (Not "minimal": some models don't expose that tier, so "low" is the safe floor.)
    classify_llm = get_langchain_llm(reasoning_effort="low")
    topic_list = "\n".join(f"- {k}: {v.get('name', k)}" for k, v in topics.items())
    # Window-derived budget for the card text instead of the old [:3000] / [:8000]
    # char cuts. Helper trims card_content to fit whatever room the template leaves.
    from backend.context_assembler import ContextBudget, Section, resolve_input_budget, count_tokens

    def _fit_card(template_filled_blank: str) -> str:
        budget = max(1000, resolve_input_budget() - count_tokens(template_filled_blank))
        return ContextBudget(budget).pack([Section("card", card_content, priority=1)]).get("card")

    try:
        resp = await classify_llm.ainvoke([HumanMessage(
            content=QA_TOPIC_CLASSIFY_PROMPT.format(
                topics=topic_list,
                card=_fit_card(QA_TOPIC_CLASSIFY_PROMPT.format(topics=topic_list, card="")),
            )
        )])
        raw_key = (resp.content or "").strip()
    except Exception as e:
        logger.warning("QA card topic classify failed: %s", e)
        return {"ok": False, "topic": None, "reason": "主题分类失败，请重试"}

    # Tolerant match: case-insensitive, against both topic keys AND display names, so a model
    # returning "Python" / "Python后端" / "PYTHON" still resolves to the key.
    key = _resolve_topic_key(raw_key, topics)
    if not key:
        return {"ok": False, "topic": None, "reason": "这张卡片与现有知识库主题都不太匹配，未收录"}

    # 2) factualize / clean before it becomes retrieval "knowledge". "low" effort is plenty
    #    for extract-and-rewrite and avoids the multi-minute think a reasoning model would do.
    clean_llm = get_langchain_llm(reasoning_effort="low")
    try:
        resp2 = await clean_llm.ainvoke([HumanMessage(
            content=QA_KNOWLEDGE_CLEAN_PROMPT.format(card=_fit_card(QA_KNOWLEDGE_CLEAN_PROMPT.format(card="")))
        )])
        cleaned = (resp2.content or "").strip()
    except Exception as e:
        logger.warning("QA card clean failed: %s", e)
        return {"ok": False, "topic": None, "reason": "清洗失败，请重试"}

    if not cleaned or cleaned.upper() == "NONE" or len(cleaned) < 20:
        return {"ok": False, "topic": None, "reason": "卡片中没有适合长期收录的确定知识，未收录"}

    # 3) write to a provenance-marked, QA-specific file under the topic dir.
    topic_dir = _get_topic_dir(key, user_id)
    if topic_dir is None:
        return {"ok": False, "topic": None, "reason": "主题目录不存在，未收录"}
    target = topic_dir / "用户沉淀_qa.md"
    cleaned = _cap_deposit(cleaned)
    from datetime import datetime
    header = f"\n\n---\n\n<!-- 问答演练场收录 {datetime.now().strftime('%Y-%m-%d %H:%M')} -->\n\n"

    def _dedup_append() -> bool:
        """Read-dedup-write under one critical section so a concurrent ingest
        can't slip a duplicate past the check. Returns False if already present."""
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if cleaned[:200] in existing:
            return False
        atomic_write_text(target, existing + header + cleaned + "\n")
        return True

    async with _writeback_lock:
        appended = await asyncio.to_thread(_dedup_append)
    if not appended:
        return {"ok": True, "topic": topics[key].get("name", key), "reason": "该内容已在知识库中"}

    # 4) incremental embed so RAG retrieval can use it without a full rebuild.
    from backend.embedding_tasks import schedule_incremental_insert
    schedule_incremental_insert(key, user_id, cleaned, source="qa_ingest")
    logger.info("QA card ingested into knowledge base: topic=%s user=%s (%d chars)", key, user_id, len(cleaned))
    return {"ok": True, "topic": topics[key].get("name", key), "reason": "已收录"}
