"""Phase 7c: seed-pool hybrid question generation.

Seed pool layout::

    data/seed_pool/{topic}.jsonl
    {"id": "py-gil-01", "question": "...", "weak_points": ["GIL 释放时机"],
     "difficulty": 3, "category": "core_concept"}

If a topic has a seed pool, ``draft_from_seed_pool`` returns up to N questions
that match the user's active weak_points. The drill pipeline then asks the
LLM only for the remaining (10 - N) questions, halving prompt size + cost.

Graceful no-op when the topic has no seed pool file.
"""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Iterable

from backend.config import settings

logger = logging.getLogger("uvicorn")


_POOL_CACHE: dict[str, tuple[float, list[dict]]] = {}   # topic → (mtime, rows)


def _pool_path(topic: str) -> Path:
    return settings.base_dir / "data" / "seed_pool" / f"{topic}.jsonl"


def _load_pool(topic: str) -> list[dict]:
    path = _pool_path(topic)
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        _POOL_CACHE[topic] = (0.0, [])
        return []

    cached = _POOL_CACHE.get(topic)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    rows: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        logger.warning("Failed to load seed pool %s: %s", path, exc)
        rows = []
    _POOL_CACHE[topic] = (mtime, rows)
    return rows


def has_pool(topic: str) -> bool:
    return bool(_load_pool(topic))


def draft_from_seed_pool(
    topic: str,
    weak_points: Iterable[str],
    *,
    n: int = 6,
    avoid_recent: Iterable[str] = (),
    seed: int | None = None,
) -> list[dict]:
    """Pick up to ``n`` seed questions that target the user's weak_points.

    Falls back to broad topic coverage if no seed targets the active WPs.
    """
    pool = _load_pool(topic)
    if not pool:
        return []

    wp_set = {w.lower() for w in weak_points if w}
    avoid_set = {q.lower()[:50] for q in avoid_recent if q}

    rng = random.Random(seed)

    # Priority 1: seeds explicitly tagged with one of the user's active WPs.
    targeted: list[dict] = []
    others: list[dict] = []
    for seed_q in pool:
        question = seed_q.get("question", "")
        if not question or question.lower()[:50] in avoid_set:
            continue
        tags = {t.lower() for t in seed_q.get("weak_points", [])}
        if tags & wp_set:
            targeted.append(seed_q)
        else:
            others.append(seed_q)

    rng.shuffle(targeted)
    rng.shuffle(others)
    picked = (targeted + others)[:n]

    # Normalize the shape so downstream code can mix seed questions with
    # LLM-generated ones without special-casing.
    out: list[dict] = []
    for i, seed_q in enumerate(picked, start=1):
        out.append({
            "id": i,
            "question": seed_q["question"],
            "difficulty": int(seed_q.get("difficulty", 3)),
            "focus_area": ", ".join(seed_q.get("weak_points", [])) or "种子题",
            "category": seed_q.get("category", "core_concept"),
            "pillar": seed_q.get("pillar", "general"),
            "source": "seed_pool",
        })
    return out
