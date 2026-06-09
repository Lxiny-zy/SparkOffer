"""Validator: ensure the batch covers most of the persona's weak_points.

The drill is supposed to be *personalized*. If the LLM ignored the weak_point
list and made up its own topics, this validator catches that.

Coverage detection runs in two stages:
1. Cheap keyword/substring match (case-insensitive).
2. Embedding-cosine fallback at threshold 0.55 for paraphrased hits.
   The embedding step reuses the Phase 0 Redis cache.
"""
from __future__ import annotations

import logging

from .base import ValidationContext, ValidationResult


logger = logging.getLogger("uvicorn")


EMBED_HIT_THRESHOLD = 0.55
MAX_BAD_QUESTIONS = 4


class WeakPointCoverageValidator:
    name = "weak_point_coverage"

    def validate(self, questions: list[dict], ctx: ValidationContext) -> ValidationResult:
        if not ctx.weak_points or not questions:
            return ValidationResult(ok=True, summary="no weak_points configured — skipped")

        covered_wps: set[str] = set()
        wp_to_questions: dict[str, list[int]] = {wp: [] for wp in ctx.weak_points}

        # Cheap pass: substring match.
        unmatched: list[dict] = []
        for q in questions:
            text = (q.get("question", "") + " " + q.get("focus_area", "")).lower()
            hit = None
            for wp in ctx.weak_points:
                if wp and wp.lower() in text:
                    hit = wp
                    break
            if hit:
                covered_wps.add(hit)
                wp_to_questions[hit].append(q.get("id"))
            else:
                unmatched.append(q)

        # Embedding pass — only run if substring didn't already cover enough.
        target = max(1, int(round(len(ctx.weak_points) * ctx.expected_weak_point_coverage)))
        if len(covered_wps) < target and unmatched:
            try:
                self._fill_with_embeddings(unmatched, ctx, covered_wps, wp_to_questions)
            except Exception as exc:
                logger.warning("WeakPointCoverage embedding pass failed: %s", exc)

        coverage = len(covered_wps) / max(1, len(ctx.weak_points))
        if coverage >= ctx.expected_weak_point_coverage:
            return ValidationResult(
                ok=True,
                summary=f"coverage {coverage:.0%} ≥ target {ctx.expected_weak_point_coverage:.0%}",
            )

        # Pick the questions that don't cover any WP as repair candidates.
        bad_ids: list[int] = []
        reasons: dict[int, str] = {}
        missing_wps = [wp for wp in ctx.weak_points if wp not in covered_wps]
        for q in unmatched[:MAX_BAD_QUESTIONS]:
            qid = q.get("id")
            if qid is None:
                continue
            bad_ids.append(qid)
            target_wp = missing_wps[(len(bad_ids) - 1) % max(1, len(missing_wps))] if missing_wps else "未覆盖的薄弱点"
            reasons[qid] = (
                f'本题没有覆盖任何用户薄弱点。请改成针对 weak_point: "{target_wp}" 的题目，'
                f'要求题干必须显式触及该薄弱点的核心机制。'
            )

        return ValidationResult(
            ok=False, bad_ids=bad_ids, reasons=reasons,
            summary=f"coverage {coverage:.0%} < target {ctx.expected_weak_point_coverage:.0%}",
        )

    def _fill_with_embeddings(
        self,
        unmatched: list[dict],
        ctx: ValidationContext,
        covered_wps: set,
        wp_to_questions: dict,
    ) -> None:
        """Use Redis-cached embeddings to detect paraphrased coverage.

        Mutates ``unmatched`` in place: any question matched via embedding is
        removed so the caller's repair sweep doesn't flag a question that is
        actually covered.
        """
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

        wp_embeddings = []
        wp_keys = []
        for wp in ctx.weak_points:
            if wp in covered_wps:
                continue
            emb = _embed(wp)
            if emb is not None:
                wp_embeddings.append(emb)
                wp_keys.append(wp)
        if not wp_embeddings:
            return

        matched_qids: set = set()
        for q in unmatched:
            qtext = (q.get("question") or "") + " " + (q.get("focus_area") or "")
            emb = _embed(qtext)
            if emb is None:
                continue
            # 混维度守卫：换过 embedding 模型后 wp 缓存里可能残留旧维度向量，
            # np.stack 后与本题 emb 做 cosine 会 ValueError 打断出题。只保留与 emb
            # 同维度的 wp 行（keys 同步过滤）；过滤后为空则当作未命中，安全跳过。
            rows = [(k, e) for k, e in zip(wp_keys, wp_embeddings) if e.shape == emb.shape]
            if not rows:
                continue
            safe_keys = [k for k, _ in rows]
            matrix = np.stack([e for _, e in rows])
            sims = _cosine_similarity(emb, matrix)
            best_idx = int(np.argmax(sims))
            if float(sims[best_idx]) >= EMBED_HIT_THRESHOLD:
                wp = safe_keys[best_idx]
                covered_wps.add(wp)
                wp_to_questions[wp].append(q.get("id"))
                matched_qids.add(q.get("id"))

        # Drop questions we just covered so the repair sweep doesn't re-flag them.
        if matched_qids:
            unmatched[:] = [q for q in unmatched if q.get("id") not in matched_qids]
