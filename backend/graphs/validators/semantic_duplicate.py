"""Validator: drop questions that look like recent ones or duplicate each other.

Uses Redis-cached embeddings (Phase 0). The cosine threshold is intentionally
high (0.9) — the dedup validator should only fire on near-identical paraphrases,
not on questions that happen to share a topic.
"""
from __future__ import annotations

import logging

from .base import ValidationContext, ValidationResult


logger = logging.getLogger("uvicorn")


DUP_THRESHOLD = 0.90
MAX_BAD_QUESTIONS = 4


class SemanticDuplicateValidator:
    name = "semantic_duplicate"

    def validate(self, questions: list[dict], ctx: ValidationContext) -> ValidationResult:
        if not questions:
            return ValidationResult(ok=True, summary="empty")

        try:
            import numpy as np

            from backend.redis_cache import get_cache
            from backend.vector_memory import _cosine_similarity

            cache = get_cache()

            def _embed(text: str):
                cached = cache.get_embedding(text)
                if cached is not None:
                    return cached
                try:
                    from backend.llm_provider import get_embedding
                    vec = get_embedding().get_text_embedding(text)
                    arr = np.asarray(vec, dtype=np.float32)
                    cache.set_embedding(text, arr)
                    return arr
                except Exception:
                    return None

            recent_embs = [e for e in (_embed(q) for q in ctx.recent_questions[-20:] if q) if e is not None]

            def _dim_safe_matrix(rows, ref):
                """混维度守卫：换过 embedding 模型后缓存里可能残留旧维度向量，
                直接 np.stack 后与 ref 做 cosine 会 ValueError 打断出题。这里只保留
                与 ref 同维度的行；过滤后为空返回 None（视为无可比项，安全跳过）。"""
                same = [r for r in rows if r.shape == ref.shape]
                return np.stack(same) if same else None

            bad_ids: list[int] = []
            reasons: dict[int, str] = {}
            kept_embs: list[np.ndarray] = []
            for q in questions:
                qtext = q.get("question") or ""
                if not qtext:
                    continue
                emb = _embed(qtext)
                if emb is None:
                    # Don't push None into kept_embs — the later np.stack would
                    # filter it out and fall over when *every* embed failed.
                    continue
                # Check against recent history.
                recent_matrix = _dim_safe_matrix(recent_embs, emb)
                if recent_matrix is not None:
                    sims = _cosine_similarity(emb, recent_matrix)
                    if float(sims.max()) >= DUP_THRESHOLD:
                        bad_ids.append(q.get("id"))
                        reasons[q.get("id")] = "本题与最近 20 道历史题重复（cosine ≥ 0.9）。请改一道明显不同的题。"
                        # A rejected duplicate must NOT become a reference vector —
                        # otherwise a later question gets compared against a question
                        # we already threw away, corrupting the dedup decision.
                        continue
                # Check against earlier questions in the same batch.
                batch_matrix = _dim_safe_matrix(kept_embs, emb)
                if batch_matrix is not None:
                    sims = _cosine_similarity(emb, batch_matrix)
                    if float(sims.max()) >= DUP_THRESHOLD:
                        bad_ids.append(q.get("id"))
                        reasons[q.get("id")] = "本题与同批早一题重复。请改成考察不同角度。"
                        continue
                kept_embs.append(emb)

            bad_ids = [i for i in bad_ids if i is not None][:MAX_BAD_QUESTIONS]
            if not bad_ids:
                return ValidationResult(ok=True, summary="no semantic duplicates")
            return ValidationResult(
                ok=False, bad_ids=bad_ids, reasons=reasons,
                summary=f"{len(bad_ids)} semantic duplicate(s)",
            )
        except Exception as exc:
            logger.warning("SemanticDuplicate validator skipped: %s", exc)
            return ValidationResult(ok=True, summary=f"skipped ({exc})")
