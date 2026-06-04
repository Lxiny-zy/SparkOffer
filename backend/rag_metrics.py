"""RAG quality metrics — embedding-based retrieval metrics + LLM-based generation metrics.

Retrieval metrics (zero extra LLM cost):
- Context Relevance: mean cosine(query_emb, chunk_emb)
- Context Precision: Average Precision — are relevant chunks ranked higher?
- Context Recall: fraction of weak_points covered by at least one chunk.
  ``None`` when there are no weak_points to measure against — recall is
  undefined without something to recall, and a fabricated number would
  silently pollute the dashboard.

Generation metrics (extracted from existing LLM eval response):
- Faithfulness: is the answer grounded in RAG context? (0-10)
- Answer Relevance: does the answer address the question? (0-10)
- Answer Correctness: weighted composite (0-10)

All metrics are stored as 0-1 floats internally, displayed as 0-100% on frontend.
``compute_retrieval_metrics`` returns ``None`` (not all-zeros) when it can't
measure — callers must treat that as "not measured", never persist it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from backend.vector_store.base import _cosine_similarity

logger = logging.getLogger("uvicorn")

RELEVANCE_THRESHOLD = 0.5


def clamp_score_0_10(value) -> float | None:
    """Clamp an LLM-emitted 0-10 score into range; reject bool / non-numeric.

    Mirrors resume_interview._parse_inline_eval — models occasionally emit
    out-of-range values (a percentage, >10, negative) or a bare bool. Those
    must not leak through as >100% readings in the dashboard / Review badges.
    Returns None for unusable input so callers can drop it.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(10.0, float(value)))


@dataclass
class RetrievalMetrics:
    context_relevance: float = 0.0
    context_precision: float = 0.0
    context_recall: float | None = None
    chunk_details: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "context_relevance": round(self.context_relevance, 4),
            "context_precision": round(self.context_precision, 4),
            "context_recall": (
                round(self.context_recall, 4) if self.context_recall is not None else None
            ),
            "chunk_details": self.chunk_details,
        }


@dataclass
class GenerationMetrics:
    faithfulness: float = 0.0
    answer_relevance: float = 0.0
    answer_correctness: float = 0.0

    def to_dict(self) -> dict:
        return {
            "faithfulness": round(self.faithfulness, 4),
            "answer_relevance": round(self.answer_relevance, 4),
            "answer_correctness": round(self.answer_correctness, 4),
        }


async def compute_retrieval_metrics(
    query_texts: list[str],
    chunks: list[str],
    chunk_embeddings: list[np.ndarray | None],
    chunk_sources: list[str],
    weak_points: list[str],
) -> RetrievalMetrics | None:
    """Compute retrieval quality metrics using embeddings only.

    Returns ``None`` when metrics cannot be measured (no chunks, every chunk or
    query embedding missing, or a dimension mismatch from a stale embedding
    cache after a model switch). Callers must NOT persist a None result — a
    fabricated all-zeros record is indistinguishable from genuinely poor
    retrieval and corrupts the trend charts.
    """
    if not chunks or not chunk_embeddings:
        return None

    valid_chunk_embs = [e for e in chunk_embeddings if e is not None]
    if not valid_chunk_embs:
        return None

    # Dimension guard: a stale cache can mix vectors from a previous embedding
    # model. vstack/cosine over mismatched dims would raise — treat as "not
    # measured" instead of letting the caller's except swallow a crash.
    chunk_dims = {e.shape[0] for e in valid_chunk_embs}
    if len(chunk_dims) != 1:
        logger.warning("RAG metrics: mixed chunk-embedding dims %s; skipping", chunk_dims)
        return None
    chunk_dim = next(iter(chunk_dims))
    chunk_matrix = np.vstack(valid_chunk_embs)

    # Embed queries + weak_points in ONE batch and reuse. weak_points are a
    # subset of the queries that drove retrieval, so the previous code embedded
    # them twice (relevance loop + recall) on the question-gen hot path.
    emb_map = await _embed_unique([*query_texts, *(weak_points or [])])

    query_embs = [emb_map.get(t) for t in query_texts]
    valid_query_embs = [e for e in query_embs if e is not None and e.shape[0] == chunk_dim]
    if not valid_query_embs:
        return None
    all_query_matrix = np.vstack(valid_query_embs)

    # Context Relevance: mean of max cosine(query, chunk) across queries
    relevance_scores: list[float] = []
    for q_emb in valid_query_embs:
        sims = _cosine_similarity(q_emb, chunk_matrix)
        relevance_scores.append(float(np.max(sims)))
    context_relevance = float(np.mean(relevance_scores)) if relevance_scores else 0.0

    # Per-chunk scores (max cosine to any query) — aligned to `chunks` order.
    per_chunk_scores: list[float] = []
    for emb in chunk_embeddings:
        if emb is not None and emb.shape[0] == chunk_dim:
            sims = _cosine_similarity(emb, all_query_matrix)
            per_chunk_scores.append(float(np.max(sims)))
        else:
            per_chunk_scores.append(0.0)

    # Context Precision: Average Precision — relevant chunks should rank higher
    context_precision = _average_precision(per_chunk_scores, RELEVANCE_THRESHOLD)

    # Context Recall: fraction of weak_points covered by at least one chunk.
    wp_embs = [emb_map.get(wp) for wp in (weak_points or [])]
    context_recall = _compute_recall(wp_embs, chunk_matrix, chunk_dim, weak_points or [])

    chunk_details = [
        {"score": round(s, 4), "source": src}
        for s, src in zip(per_chunk_scores, chunk_sources)
    ]

    return RetrievalMetrics(
        context_relevance=max(0.0, min(1.0, context_relevance)),
        context_precision=max(0.0, min(1.0, context_precision)),
        context_recall=context_recall,
        chunk_details=chunk_details,
    )


def extract_generation_metrics(per_question_scores: list[dict]) -> GenerationMetrics | None:
    """Extract generation metrics from LLM eval response fields.

    Expects each score dict to optionally contain:
    - faithfulness_score: int 0-10
    - answer_relevance_score: int 0-10
    Out-of-range / non-numeric values are clamped or dropped via clamp_score_0_10.
    """
    faithfulness_vals: list[float] = []
    relevance_vals: list[float] = []

    for s in per_question_scores:
        if s.get("skipped"):
            continue
        fs = clamp_score_0_10(s.get("faithfulness_score"))
        ar = clamp_score_0_10(s.get("answer_relevance_score"))
        if fs is not None:
            faithfulness_vals.append(fs / 10.0)
        if ar is not None:
            relevance_vals.append(ar / 10.0)

    if not faithfulness_vals and not relevance_vals:
        return None

    faith = float(np.mean(faithfulness_vals)) if faithfulness_vals else 0.0
    relevance = float(np.mean(relevance_vals)) if relevance_vals else 0.0
    correctness = faith * 0.4 + relevance * 0.6

    return GenerationMetrics(
        faithfulness=faith,
        answer_relevance=relevance,
        answer_correctness=correctness,
    )


def _average_precision(scores: list[float], threshold: float) -> float:
    """Compute Average Precision: reward relevant chunks ranked higher."""
    if not scores:
        return 0.0
    relevant_count = 0
    precision_sum = 0.0
    for i, s in enumerate(scores):
        if s >= threshold:
            relevant_count += 1
            precision_sum += relevant_count / (i + 1)
    if relevant_count == 0:
        return 0.0
    return precision_sum / relevant_count


async def _embed_unique(texts: list[str]) -> dict[str, np.ndarray]:
    """Embed each distinct text once via the cached batch embedder.

    Returns text→embedding, skipping any text whose embedding failed (None).
    """
    uniq = list(dict.fromkeys(t for t in texts if t))
    if not uniq:
        return {}
    from backend.graphs.rag_retrieval import _embed_many
    embeddings, _, _ = await _embed_many(uniq)
    return {t: e for t, e in zip(uniq, embeddings) if e is not None}


def _compute_recall(
    wp_embeddings: list[np.ndarray | None],
    chunk_matrix: np.ndarray,
    chunk_dim: int,
    weak_points: list[str],
) -> float | None:
    """Fraction of weak_points whose best chunk cosine ≥ threshold.

    Returns None when recall is unmeasurable — no weak_points to recall, or
    none of them could be embedded. The rag_metrics.context_recall column is
    nullable and the frontend renders None as "--".
    """
    if not weak_points:
        return None
    covered = 0
    measured = 0
    for emb in wp_embeddings:
        if emb is None or emb.shape[0] != chunk_dim:
            continue
        measured += 1
        sims = _cosine_similarity(emb, chunk_matrix)
        if float(np.max(sims)) >= RELEVANCE_THRESHOLD:
            covered += 1
    if measured == 0:
        return None
    return covered / measured
