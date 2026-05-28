"""Weak-point coverage judge.

For each persona weak_point, check if at least one generated question
covers it via either:
    (a) lowercase substring match (any 4+ char token from the weak_point
        appears in the question text), OR
    (b) embedding cosine ≥ 0.55 against any question.

Score = covered_wps / total_wps. Fail-soft: if embedding fails for a WP we
fall back to keyword match only and log a warning.
"""
from __future__ import annotations

import asyncio
import logging
import re

import numpy as np

from backend.vector_memory import _cosine_similarity

logger = logging.getLogger("uvicorn")

COSINE_THRESHOLD = 0.55
KEYWORD_MIN_LEN = 4


def _extract_tokens(text: str) -> list[str]:
    """Pull tokens of length ≥4 (CJK or alnum) from a weak_point string."""
    # CJK runs of 2+ chars + ascii words of 4+ chars
    cjk = re.findall(r"[一-鿿]{2,}", text)
    en = re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text)
    return [t for t in cjk + en if len(t) >= 2]


def _keyword_hit(wp_text: str, question_text: str) -> bool:
    q = question_text.lower()
    tokens = _extract_tokens(wp_text.lower())
    if not tokens:
        return False
    # Require at least one ≥4-char token OR two ≥2-char CJK tokens to hit
    long_hits = [t for t in tokens if len(t) >= KEYWORD_MIN_LEN and t in q]
    if long_hits:
        return True
    short_cjk_hits = [t for t in tokens if len(t) >= 2 and t in q]
    return len(short_cjk_hits) >= 2


async def _embed_texts(texts: list[str]) -> list[np.ndarray] | None:
    """Embed via the project's vector_memory helper. Returns None on hard failure."""
    try:
        from backend.vector_memory import _embed_batch
        return await _embed_batch(texts)
    except Exception as e:
        logger.warning("Coverage judge: embedding call failed (%s), falling back to keyword-only", e)
        return None


class CoverageJudge:
    name = "coverage"

    async def evaluate(self, persona: dict, questions: list[dict]) -> tuple[float, str]:
        wps = [w.get("point", "") for w in persona.get("weak_points", []) if w.get("point")]
        if not wps:
            # Persona has no weak points (e.g. cold_start). Coverage is
            # undefined — return 1.0 so this judge doesn't penalize the
            # cold-start case where personalization can't do anything anyway.
            return 1.0, "no_weak_points"
        if not questions:
            return 0.0, f"0/{len(wps)} (no questions)"

        q_texts = [str(q.get("question", "")) for q in questions]

        # Phase 1: cheap keyword pass
        covered_idx: set[int] = set()
        for i, wp in enumerate(wps):
            if any(_keyword_hit(wp, qt) for qt in q_texts):
                covered_idx.add(i)

        # Phase 2: embedding fallback for uncovered WPs only (saves API calls)
        uncovered = [i for i in range(len(wps)) if i not in covered_idx]
        if uncovered:
            embeddings = await _embed_texts(q_texts + [wps[i] for i in uncovered])
            if embeddings and len(embeddings) == len(q_texts) + len(uncovered):
                q_mat = np.stack(embeddings[:len(q_texts)]) if q_texts else None
                if q_mat is not None:
                    for offset, wp_idx in enumerate(uncovered):
                        wp_vec = embeddings[len(q_texts) + offset]
                        if np.linalg.norm(wp_vec) < 1e-9:
                            continue
                        sims = _cosine_similarity(wp_vec, q_mat)
                        if sims.max() >= COSINE_THRESHOLD:
                            covered_idx.add(wp_idx)

        score = len(covered_idx) / len(wps)
        detail = f"{len(covered_idx)}/{len(wps)} wps covered"
        return score, detail
