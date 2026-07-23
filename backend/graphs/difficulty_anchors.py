"""Phase 5B: in-process difficulty anchor cache + calibration.

Anchors live on disk as data/anchors/{topic}.json — generated offline by
scripts/bootstrap_difficulty_anchors.py. At startup (or first lookup) we
mmap them into a per-topic numpy matrix for fast cosine k-NN.

If a topic has no anchor file we degrade gracefully: ``calibrate`` returns
the input unchanged and logs once.
"""
from __future__ import annotations

import json
import logging
import threading

import numpy as np

from backend.config import settings
from backend.vector_memory import _cosine_similarity

logger = logging.getLogger("uvicorn")


_CACHE_LOCK = threading.Lock()
_ANCHOR_CACHE: dict[str, dict] = {}   # topic → {"matrix": np.ndarray, "difficulties": list[int]}
_LOGGED_MISSING: set[str] = set()


def _load_topic_anchors(topic: str) -> dict | None:
    """Lazy-load anchors for a topic on first use. Returns None if no file."""
    with _CACHE_LOCK:
        if topic in _ANCHOR_CACHE:
            return _ANCHOR_CACHE[topic]
        path = settings.base_dir / "data" / "anchors" / f"{topic}.json"
        if not path.exists():
            if topic not in _LOGGED_MISSING:
                logger.info("No difficulty anchors at %s (run scripts/bootstrap_difficulty_anchors)", path)
                _LOGGED_MISSING.add(topic)
            return None
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to parse anchor file %s: %s", path, exc)
            return None

        matrix_rows = []
        difficulties = []
        for row in rows:
            emb = row.get("embedding")
            if not emb:
                continue
            matrix_rows.append(np.asarray(emb, dtype=np.float32))
            difficulties.append(int(row.get("difficulty", 3)))
        if not matrix_rows:
            return None
        bundle = {
            "matrix": np.stack(matrix_rows),
            "difficulties": difficulties,
        }
        _ANCHOR_CACHE[topic] = bundle
        logger.info("Loaded %d difficulty anchors for topic=%s", len(difficulties), topic)
        return bundle


def calibrate_difficulties(
    topic: str,
    questions: list[dict],
    embed_fn=None,
    *,
    k: int = 3,
) -> tuple[int, int]:
    """Override each question's ``difficulty`` field by k-NN against anchors.

    Args:
        topic: topic key (e.g. "python")
        questions: list of question dicts; mutated in place.
        embed_fn: callable(text) → np.ndarray|None. Defaults to using the
            Redis-cached embedding helper.
        k: number of neighbors to average.

    Returns:
        (calibrated_count, total_count) — how many questions had their
        difficulty changed by ≥ 1.
    """
    bundle = _load_topic_anchors(topic)
    if bundle is None or not questions:
        return 0, len(questions)

    matrix = bundle["matrix"]
    difficulties = bundle["difficulties"]

    if embed_fn is None:
        embed_fn = _default_embed

    calibrated = 0
    for q in questions:
        text = q.get("question") or ""
        if not text:
            continue
        try:
            emb = embed_fn(text)
        except Exception as exc:
            logger.warning("calibrate: embed failed for q.id=%s: %s", q.get("id"), exc)
            continue
        if emb is None:
            continue
        sims = _cosine_similarity(emb, matrix)
        # Top-k indices by similarity (descending).
        top_idx = np.argsort(-sims)[:k]
        weights = sims[top_idx]
        weights = np.where(weights > 0, weights, 0.0)
        if weights.sum() <= 0:
            continue
        new_diff = float(np.dot(weights, np.array([difficulties[i] for i in top_idx])) / weights.sum())
        new_diff_int = max(1, min(5, int(round(new_diff))))
        old_diff = int(q.get("difficulty", 3))
        if abs(new_diff_int - old_diff) >= 1:
            q["difficulty_llm"] = old_diff
            q["difficulty"] = new_diff_int
            calibrated += 1

    return calibrated, len(questions)


def _default_embed(text: str):
    """Default embedding lookup — Redis cache → on-miss compute → cache write."""
    from backend.redis_cache import get_cache

    cache = get_cache()
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
