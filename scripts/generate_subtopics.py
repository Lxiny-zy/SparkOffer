"""One-shot offline script: generate per-topic sub-topic lists.

Output: data/topic_subtopics.json with shape::

    {
        "python": ["GIL", "asyncio", "descriptor protocol", ...],
        "java": [...],
        ...
    }

Used by the Phase 3 RAG retrieval layer as the "exploration query" companion.
Run once after onboarding a new topic catalog; safe to re-run idempotently
(overwrites the file).

Usage:
    python -m scripts.generate_subtopics            # all topics
    python -m scripts.generate_subtopics --user default0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage


PROMPT_TEMPLATE = """你是技术面试专家。请为「{topic_name}」这个技术板块列出 8-10 个**核心子领域**关键词或短语。

要求：
- 每个子领域 2-6 个汉字或英文术语，不要写句子。
- 覆盖该板块的不同维度（语言特性 / 框架 / 工程实践 / 性能 / 安全 等），不要只挑同一维度。
- 输出 JSON 数组，例如 ["GIL 机制", "asyncio 边界", ...]，不要任何额外说明。
"""


def generate_for_topic(topic_key: str, topic_name: str) -> list[str]:
    from backend.llm_provider import get_langchain_llm

    llm = get_langchain_llm()
    response = llm.invoke([
        SystemMessage(content="你是技术面试出题专家，只返回 JSON 数组。"),
        HumanMessage(content=PROMPT_TEMPLATE.format(topic_name=topic_name)),
    ])
    raw = response.content if hasattr(response, "content") else str(response)
    # Cheap-but-robust JSON extraction: find first '[' and balance brackets.
    start = raw.find("[")
    if start < 0:
        raise ValueError(f"No JSON array found in response: {raw[:200]}")
    depth = 0
    end = -1
    for i, ch in enumerate(raw[start:], start=start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise ValueError(f"Unterminated JSON array in response: {raw[:200]}")
    arr = json.loads(raw[start:end])
    if not isinstance(arr, list):
        raise ValueError(f"Expected list, got {type(arr).__name__}")
    return [str(x).strip() for x in arr if str(x).strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="default0",
                        help="user_id to load topics from (default: default0)")
    parser.add_argument("--out", default=None, help="output path (default: data/topic_subtopics.json)")
    args = parser.parse_args()

    from backend.config import settings
    from backend.indexer import load_topics

    topics = load_topics(args.user)
    if not topics:
        print(f"No topics found for user={args.user}", file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else settings.base_dir / "data" / "topic_subtopics.json"

    # Load existing so we can merge — re-runs only refresh missing topics
    # unless the user explicitly wants to overwrite.
    existing: dict[str, list[str]] = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    for key, meta in topics.items():
        name = meta.get("name", key) if isinstance(meta, dict) else str(meta)
        print(f"[generate] {key} ({name}) ...", flush=True)
        try:
            existing[key] = generate_for_topic(key, name)
            print(f"   → {len(existing[key])} sub-topics")
        except Exception as exc:
            print(f"   ! failed: {exc}", file=sys.stderr)

    out_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
