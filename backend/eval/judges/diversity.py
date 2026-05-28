"""Diversity judge — pairwise embedding cosine.

Score = 1 - mean(pairwise cosine). Higher = more diverse (questions cover
different concepts). If embedding fails entirely the judge returns 0.0
with a "embed_failed" detail so it doesn't blow up the whole run.
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("uvicorn")


async def _embed_texts(texts: list[str]) -> list[np.ndarray] | None:
    try:
        from backend.vector_memory import _embed_batch
        return await _embed_batch(texts)
    except Exception as e:
        logger.warning("Diversity judge: embedding failed (%s)", e)
        return None


class DiversityJudge:
    name = "diversity"

    async def evaluate(self, persona: dict, questions: list[dict]) -> tuple[float, str]:
        texts = [str(q.get("question", "")) for q in questions if q.get("question")]
        if len(texts) < 2:
            return 0.0, f"only {len(texts)} questions"

        embeddings = await _embed_texts(texts)
        if not embeddings or len(embeddings) != len(texts):
            return 0.0, "embed_failed"

        matrix = np.stack(embeddings).astype(np.float32)
        # Skip zero vectors (failed embeddings) — if too many failed, bail.
        norms = np.linalg.norm(matrix, axis=1)
        live_mask = norms > 1e-6
        if live_mask.sum() < 2:
            return 0.0, "all_embeddings_zero"
        matrix = matrix[live_mask]
        norms = norms[live_mask]

        normed = matrix / norms[:, None]
        sim = normed @ normed.T  # (n, n)

        # Upper triangle without diagonal
        n = sim.shape[0]
        iu = np.triu_indices(n, k=1)
        pairwise = sim[iu]
        mean_cos = float(pairwise.mean())
        score = max(0.0, min(1.0, 1.0 - mean_cos))
        return score, f"n={n} mean_cos={mean_cos:.3f}"
