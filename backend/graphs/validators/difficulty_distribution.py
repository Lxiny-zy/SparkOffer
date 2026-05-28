"""Validator: difficulty distribution must not collapse into a single bucket.

Why: LLMs called to "generate 10 medium-difficulty questions" often produce
9 questions of identical difficulty plus 1 outlier. The downstream timeline
and per-WP mastery EWMA both lose signal when difficulty is uniform.

We check via symmetric KL divergence vs the slot plan's target distribution.
If the divergence exceeds the threshold, the most over-represented difficulty
bucket donates its excess to ``bad_ids`` for repair.
"""
from __future__ import annotations

import math

from .base import ValidationContext, ValidationResult


KL_THRESHOLD = 0.4   # empirical; raise → stricter, lower → looser
MAX_BAD_QUESTIONS = 3


class DifficultyDistributionValidator:
    name = "difficulty_distribution"

    def validate(self, questions: list[dict], ctx: ValidationContext) -> ValidationResult:
        target = ctx.target_difficulty_distribution
        if not target or not questions:
            return ValidationResult(ok=True, summary="no target — skipped")

        produced = self._distribution([q.get("difficulty", 3) for q in questions])
        target_norm = self._normalize(target)
        produced_norm = self._normalize(produced)

        kl = _symmetric_kl(produced_norm, target_norm)
        if kl <= KL_THRESHOLD:
            return ValidationResult(ok=True, summary=f"KL={kl:.3f} ≤ {KL_THRESHOLD}")

        # Find the over-represented bucket(s) and flag their excess questions.
        bad_ids: list[int] = []
        reasons: dict[int, str] = {}
        excess: dict[int, int] = {}
        for d, p in produced.items():
            target_n = target.get(d, 0)
            if p > target_n:
                excess[d] = p - target_n

        # Mark questions from the most-over bucket first, up to MAX_BAD_QUESTIONS
        for d in sorted(excess, key=lambda x: -excess[x]):
            n_to_remove = excess[d]
            count_for_d = produced.get(d, 0)
            for q in questions:
                if len(bad_ids) >= MAX_BAD_QUESTIONS or n_to_remove <= 0:
                    break
                if q.get("difficulty") == d and q.get("id") not in bad_ids:
                    bad_ids.append(q["id"])
                    reasons[q["id"]] = (
                        f"难度集中：当前难度 {d}/5 出现 {count_for_d} 次，超出策略上限。请把这题改成更稀缺的难度档（参考整批分布）。"
                    )
                    n_to_remove -= 1
            if len(bad_ids) >= MAX_BAD_QUESTIONS:
                break

        return ValidationResult(
            ok=False, bad_ids=bad_ids, reasons=reasons,
            summary=f"KL={kl:.3f} > {KL_THRESHOLD}, flagged {len(bad_ids)}",
        )

    @staticmethod
    def _distribution(values: list) -> dict[int, int]:
        dist: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for v in values:
            try:
                d = max(1, min(5, int(v)))
            except (TypeError, ValueError):
                d = 3
            dist[d] = dist.get(d, 0) + 1
        return dist

    @staticmethod
    def _normalize(d: dict[int, int]) -> dict[int, float]:
        total = sum(d.values()) or 1
        return {k: v / total for k, v in d.items()}


def _symmetric_kl(p: dict[int, float], q: dict[int, float]) -> float:
    """Symmetric KL — avoids the asymmetry surprise when one side is zero."""
    eps = 1e-6
    keys = set(p) | set(q)
    fwd = sum(p.get(k, eps) * math.log((p.get(k, eps) + eps) / (q.get(k, eps) + eps)) for k in keys)
    bwd = sum(q.get(k, eps) * math.log((q.get(k, eps) + eps) / (p.get(k, eps) + eps)) for k in keys)
    return abs(fwd) + abs(bwd)
