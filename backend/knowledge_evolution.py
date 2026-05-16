"""Knowledge evolution — auto-writeback from interviews + high-freq collection."""
import asyncio
import logging
from pathlib import Path

from backend.config import settings
from backend.llm_provider import get_langchain_llm

logger = logging.getLogger("uvicorn")

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
        response = llm.invoke([HumanMessage(content=_EXTRACT_PROMPT.format(qa_text=qa_text))])
        extracted = response.content.strip()
        if not extracted or len(extracted) < 20:
            return

        topics = _get_topic_dir(topic, user_id)
        if not topics:
            return

        target = topics / "自动沉淀.md"
        target.parent.mkdir(parents=True, exist_ok=True)

        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        from datetime import datetime
        header = f"\n\n---\n\n<!-- 自动沉淀 {datetime.now().strftime('%Y-%m-%d %H:%M')} -->\n\n"
        new_content = header + extracted + "\n"
        target.write_text(existing + new_content, encoding="utf-8")

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
            score_val = s.get("score", 5) if isinstance(s, dict) else 5
            if score_val < 6:
                q_text = questions[i].get("question", str(questions[i])) if isinstance(questions[i], dict) else str(questions[i])
                assessment = s.get("assessment", "") if isinstance(s, dict) else ""
                low_score_items.append((q_text, score_val, assessment))

        if not low_score_items:
            return

        high_freq_dir = settings.user_high_freq_path(user_id)
        high_freq_dir.mkdir(parents=True, exist_ok=True)
        filepath = high_freq_dir / f"{topic}.md"

        existing = filepath.read_text(encoding="utf-8") if filepath.exists() else ""
        from datetime import datetime
        lines = [f"\n\n<!-- {datetime.now().strftime('%Y-%m-%d %H:%M')} -->"]
        for q, score, assessment in low_score_items:
            lines.append(f"\n## Q: {q}\n得分: {score}\n评估: {assessment}\n---")

        filepath.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")
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
