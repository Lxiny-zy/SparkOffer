"""Random baseline — samples question stems from global knowledge / high_freq files.

This is a deliberately dumb strategy: it ignores the persona entirely and pulls
chunks that look like questions (lines ending in 「？」or containing 「问」). The
goal is to give the personalized strategy something concrete to beat on the
deterministic judges (coverage, difficulty_kl).
"""
from __future__ import annotations

import random
import re
from pathlib import Path

from backend.config import settings
from backend.eval.strategies.base import Strategy

# Match lines that look like questions: end with ? / ？ / 吗？ / "请...".
_QUESTION_LINE = re.compile(r".+[?？]$|^[\d①②③④⑤⑥⑦⑧⑨⑩]+[\.、].+|.+(请|如何|为什么|怎么|什么是).+")


def _harvest_questions(topic: str, limit: int = 200) -> list[str]:
    """Scan high_freq + knowledge md files for question-like lines."""
    candidates: list[str] = []
    paths: list[Path] = []
    hf = settings.high_freq_path / f"{topic}.md"
    if hf.exists():
        paths.append(hf)
    kdir = settings.knowledge_path / topic
    if kdir.exists():
        paths.extend(kdir.glob("*.md"))

    for p in paths:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("#").lstrip("-").lstrip("*").strip()
            if not line or len(line) < 8 or len(line) > 200:
                continue
            if line.startswith(("```", ">", "|")):
                continue
            if _QUESTION_LINE.match(line):
                candidates.append(line)
            if len(candidates) >= limit:
                return candidates
    return candidates


class RandomBaselineStrategy(Strategy):
    name = "random_baseline"

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)

    async def generate_questions(
        self, persona: dict, topic: str, n_questions: int = 10,
    ) -> list[dict]:
        pool = _harvest_questions(topic)
        if not pool:
            # Truly empty topic — emit a degenerate placeholder so downstream
            # judges still run (they'll just score very low).
            pool = [f"关于 {topic} 的一道基础问题？"] * n_questions

        sampled = self._rng.sample(pool, k=min(n_questions, len(pool)))
        # Pad to n_questions if pool is smaller than requested
        while len(sampled) < n_questions:
            sampled.append(self._rng.choice(pool))

        return [
            {
                "id": i + 1,
                "question": q,
                "difficulty": self._rng.randint(1, 5),  # uniform — no targeting
                "category": "random",
            }
            for i, q in enumerate(sampled)
        ]
