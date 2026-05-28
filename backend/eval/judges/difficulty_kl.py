"""Difficulty-distribution KL divergence judge.

Target distribution depends on the persona's topic mastery score:
    mastery < 30:    {1:0.05, 2:0.20, 3:0.70, 4:0.05, 5:0}
    30 ≤ mastery ≤ 60: {1:0, 2:0.10, 3:0.40, 4:0.40, 5:0.10}
    mastery > 60:    {1:0, 2:0, 3:0.20, 4:0.40, 5:0.40}

KL(produced || target) computed with Laplace smoothing (α=0.05) to avoid log(0).
Score = max(0, 1 - KL/2) so it's in [0, 1], higher is better.

For cold_start persona (no topic_mastery), treat mastery as 0 → use the
beginner target.
"""
from __future__ import annotations

import math
from collections import Counter

LAPLACE_ALPHA = 0.05
KL_SCALE = 2.0  # KL above this → score 0


def _target_for_mastery(mastery: float) -> dict[int, float]:
    if mastery < 30:
        return {1: 0.05, 2: 0.20, 3: 0.70, 4: 0.05, 5: 0.0}
    if mastery <= 60:
        return {1: 0.0, 2: 0.10, 3: 0.40, 4: 0.40, 5: 0.10}
    return {1: 0.0, 2: 0.0, 3: 0.20, 4: 0.40, 5: 0.40}


def _persona_mastery(persona: dict) -> float:
    """Pick the highest topic mastery score (proxy for 'most relevant topic')."""
    masteries = persona.get("topic_mastery", {})
    if not masteries:
        return 0.0
    scores = [
        float(m.get("score", m.get("level", 0) * 20))
        for m in masteries.values()
        if isinstance(m, dict)
    ]
    return max(scores) if scores else 0.0


def _produced_dist(questions: list[dict]) -> dict[int, float]:
    counts = Counter()
    total = 0
    for q in questions:
        d = q.get("difficulty")
        try:
            di = int(d)
        except (TypeError, ValueError):
            continue
        if 1 <= di <= 5:
            counts[di] += 1
            total += 1
    if total == 0:
        return {i: 0.0 for i in range(1, 6)}
    return {i: counts.get(i, 0) / total for i in range(1, 6)}


def _kl(p: dict[int, float], q: dict[int, float]) -> float:
    """KL(p || q) with Laplace smoothing on both."""
    total = 0.0
    for k in range(1, 6):
        p_k = p.get(k, 0.0) + LAPLACE_ALPHA
        q_k = q.get(k, 0.0) + LAPLACE_ALPHA
        total += p_k * math.log(p_k / q_k)
    return total


class DifficultyKLJudge:
    name = "difficulty_kl"

    async def evaluate(self, persona: dict, questions: list[dict]) -> tuple[float, str]:
        if not questions:
            return 0.0, "no questions"
        mastery = _persona_mastery(persona)
        target = _target_for_mastery(mastery)
        produced = _produced_dist(questions)
        kl = _kl(produced, target)
        score = max(0.0, 1.0 - kl / KL_SCALE)
        produced_str = " ".join(f"{k}={produced[k]:.2f}" for k in range(1, 6))
        return score, f"mastery={mastery:.0f} kl={kl:.3f} produced[{produced_str}]"
