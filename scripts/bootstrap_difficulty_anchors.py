"""Phase 5B: bootstrap difficulty anchors from existing high-frequency question banks.

Each topic gets a small corpus of (question, difficulty 1-5, embedding) tuples.
After generation, the drill pipeline's _stage_finalize calibrates each LLM-
self-reported difficulty by k-NN lookup against these anchors.

Why automated heuristic + LLM single-shot scoring instead of manual labels:
- ~50 questions × 10 topics × manual = 2 days work.
- LLM single-shot batch scoring lands within ±1 of human judgment ~80% of the
  time (see Phase 6 eval results when available).
- Combining heuristics with LLM scoring de-biases each: heuristic catches
  length/complexity, LLM catches conceptual depth.

Run with:
    python -m scripts.bootstrap_difficulty_anchors --user default0
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage


# Heuristic weights — empirical, easy to tune later.
COMPLEXITY_KEYWORDS = {
    # 难度 ↑
    "权衡": 1.0, "取舍": 1.0, "底层": 1.0, "源码": 1.0, "实现": 0.5,
    "边界": 0.5, "陷阱": 0.5, "性能": 0.5, "异常": 0.3, "并发": 0.5,
    "锁": 0.5, "调度": 0.5, "排序": 0.3, "回收": 0.5, "JVM": 0.5,
    "Reactor": 0.7, "AQS": 0.8, "CAS": 0.5, "happens-before": 1.0,
    # 难度 ↓
    "是什么": -0.5, "什么": -0.3, "定义": -0.5, "区别": -0.2,
}

LLM_SCORE_PROMPT = """你是面试题难度评估专家。下面是「{topic_name}」板块的若干题目，请为**每一题**估算难度（1-5 整数）：

- 1：基础定义题（"什么是 X"）
- 2：基础应用题（如何使用 X）
- 3：原理题（X 为什么这样设计）
- 4：场景设计题（怎么把 X 用在 Y 场景）
- 5：系统权衡题（X 和 Y 的取舍 / 底层源码 / 性能调优）

输入：
{questions_block}

输出：仅返回 JSON 数组 [{{ "idx": 0, "difficulty": 3 }}, ...]，长度等于输入数量。
"""


def _heuristic_score(question: str) -> float:
    """Returns a float roughly in [1, 5] based on length + keyword density."""
    base = 2.5
    # Length: longer questions tend to involve more setup/context → harder.
    length_bonus = min(1.5, len(question) / 80.0)
    keyword_bonus = sum(weight for kw, weight in COMPLEXITY_KEYWORDS.items() if kw in question)
    score = base + length_bonus + keyword_bonus
    return max(1.0, min(5.0, score))


def _parse_high_freq_md(text: str) -> list[str]:
    """Extract question-shaped lines from a high_freq markdown file.

    Heuristics: lines that end with ? / ？ / 。 and are 12-200 chars long,
    or list items starting with "-" / "*" / digit-dot.
    """
    questions: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not (12 <= len(line) <= 200):
            continue
        # Strip common list prefixes.
        line = re.sub(r"^[-*]\s+|^\d+[\.\)、]\s+", "", line)
        if not line:
            continue
        if line.endswith(("?", "？", "。", "："))  or "?" in line or "？" in line:
            questions.append(line)
    # Dedup by lowercase substring
    seen: set[str] = set()
    unique: list[str] = []
    for q in questions:
        key = q.lower()[:50]
        if key in seen:
            continue
        seen.add(key)
        unique.append(q)
    return unique


async def _llm_batch_score(topic_name: str, questions: list[str]) -> list[int]:
    """Single LLM call that scores every question in one shot."""
    from backend.llm_provider import get_langchain_llm

    if not questions:
        return []

    questions_block = "\n".join(f"{i}. {q}" for i, q in enumerate(questions))
    prompt = LLM_SCORE_PROMPT.format(topic_name=topic_name, questions_block=questions_block)
    llm = get_langchain_llm()
    resp = await llm.ainvoke([
        SystemMessage(content="你是难度估算引擎，只返回 JSON 数组。"),
        HumanMessage(content=prompt),
    ])
    raw = resp.content if hasattr(resp, "content") else str(resp)
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end < 0:
        return [3] * len(questions)
    try:
        arr = json.loads(raw[start:end + 1])
        out = [3] * len(questions)
        for entry in arr:
            idx = int(entry.get("idx", -1))
            diff = int(entry.get("difficulty", 3))
            if 0 <= idx < len(questions):
                out[idx] = max(1, min(5, diff))
        return out
    except (json.JSONDecodeError, ValueError, TypeError):
        return [3] * len(questions)


def _combine(heuristic: float, llm: int) -> int:
    """Average heuristic + LLM score, clamp to 1-5 integer."""
    avg = (heuristic + llm) / 2.0
    return max(1, min(5, round(avg)))


async def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts using the configured backend."""
    from backend.llm_provider import get_embedding

    embed_model = get_embedding()
    out: list[list[float]] = []
    for text in texts:
        try:
            vec = await asyncio.to_thread(embed_model.get_text_embedding, text)
            out.append(list(vec))
        except Exception as exc:
            print(f"  ! embed failed for {text[:40]}: {exc}", file=sys.stderr)
            out.append([])
    return out


async def bootstrap_anchors(user_id: str, output_dir: Path | None = None) -> dict[str, int]:
    from backend.config import settings
    from backend.indexer import load_topics

    topics = load_topics(user_id)
    if not topics:
        print(f"No topics for user={user_id}", file=sys.stderr)
        return {}

    out_dir = output_dir or settings.base_dir / "data" / "anchors"
    out_dir.mkdir(parents=True, exist_ok=True)

    high_freq_dir = settings.user_high_freq_path(user_id)
    summary: dict[str, int] = {}

    for topic_key, meta in topics.items():
        topic_name = meta.get("name", topic_key) if isinstance(meta, dict) else str(meta)
        md_path = high_freq_dir / f"{topic_key}.md"
        if not md_path.exists():
            print(f"[skip] {topic_key} (no {md_path})")
            continue

        questions = _parse_high_freq_md(md_path.read_text(encoding="utf-8"))[:60]
        if not questions:
            print(f"[skip] {topic_key} (no question lines)")
            continue

        print(f"[anchor] {topic_key}: {len(questions)} candidate questions")
        llm_scores = await _llm_batch_score(topic_name, questions)
        h_scores = [_heuristic_score(q) for q in questions]
        difficulties = [_combine(h, l) for h, l in zip(h_scores, llm_scores)]

        embeddings = await _embed_batch(questions)
        rows = [
            {"question": q, "difficulty": d, "embedding": e}
            for q, d, e in zip(questions, difficulties, embeddings)
            if e   # skip rows where embedding failed
        ]
        out_path = out_dir / f"{topic_key}.json"
        out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   → {len(rows)} anchors saved to {out_path}")
        summary[topic_key] = len(rows)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="default0")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    summary = asyncio.run(bootstrap_anchors(args.user, Path(args.out) if args.out else None))
    print(f"\nDone. Anchors per topic: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
