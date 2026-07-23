"""RAG quality metrics — embedding-based retrieval metrics + LLM-based generation metrics.

These are the **online, zero-LLM-cost health gauges** computed on the question-gen
hot path. They are NOT ground-truth precision/recall — for that, see the offline
RAGAS benchmark in ``backend/rag_eval.py`` (synthesizes a golden set with known
provenance). The two systems are different scales and are NOT comparable.

Why the previous precision/recall were dropped: chunks are retrieved by cosine to
the query set, so scoring those same chunks against those same queries is circular
— Average Precision was pinned at ~1.0 and recall at ~100%. Replaced with three
non-circular signals measured from the embeddings we already have:

Retrieval metrics (zero extra LLM cost, no ground truth):
- Relevance: mean over queries of max cosine(query, chunk). How well retrieved
  content fits the query. Naturally lands ~0.45-0.65 (short query vs long doc).
- Coverage: fraction of queries whose best retrieved chunk clears COVERAGE_FLOOR
  cosine. Measures *breadth* — did every weak_point get some on-topic material,
  or did one go unserved? Replaced the old "discrimination = top1−top2 cosine",
  which was inverted for this use case: returning several equally-relevant chunks
  per query (exactly what question-gen wants) drove discrimination toward zero,
  so a healthy spread retrieval scored "poor". Coverage rewards that spread.
- Diversity: 1 − mean pairwise cosine across the final chunks. Catches retrieval
  collapse (a pile of near-duplicate passages). Reuses the chunk embeddings the
  dedup stage already computed — zero extra embedding cost.

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
import math
from dataclasses import dataclass, field

import numpy as np

from backend.vector_store.base import _cosine_similarity

logger = logging.getLogger("uvicorn")

RELEVANCE_THRESHOLD = 0.5
COVERAGE_FLOOR = 0.5   # a query (weak_point) counts as "covered" when its best
                       # retrieved chunk's cosine clears this floor


def clamp_score_0_10(value) -> float | None:
    """Clamp an LLM-emitted 0-10 score into range; reject bool / non-numeric.

    Mirrors resume_interview._parse_inline_eval — models occasionally emit
    out-of-range values (a percentage, >10, negative) or a bare bool. Those
    must not leak through as >100% readings in the dashboard / Review badges.
    Returns None for unusable input so callers can drop it.

    Numeric strings ("8", "8.0", " 7 ") are coerced to float: smaller/cheaper
    models (the decoupled small tier) often emit scores as strings, and silently
    dropping them used to None-out the generation gauges (faithfulness /
    answer_relevance) and skip the whole answer_eval row for that session.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except (ValueError, AttributeError):
            return None
    if not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return max(0.0, min(10.0, numeric))


@dataclass
class RetrievalMetrics:
    relevance: float = 0.0
    coverage: float = 0.0
    diversity: float = 0.0
    chunk_details: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "relevance": round(self.relevance, 4),
            "coverage": round(self.coverage, 4),
            "diversity": round(self.diversity, 4),
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
) -> RetrievalMetrics | None:
    """Compute retrieval quality metrics using embeddings only (no ground truth).

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

    emb_map = await _embed_unique(query_texts)
    query_embs = [emb_map.get(t) for t in query_texts]
    valid_query_embs = [e for e in query_embs if e is not None and e.shape[0] == chunk_dim]
    if not valid_query_embs:
        return None

    # Relevance: mean over queries of max cosine(query, chunk) — average fit.
    # Coverage: fraction of queries whose best chunk clears COVERAGE_FLOOR —
    # breadth, i.e. did every weak_point get some on-topic material. This is
    # NOT circular: it asks "is there a chunk above an absolute bar", not
    # "do these chunks beat the queries that retrieved them".
    relevance_scores: list[float] = []
    covered = 0
    for q_emb in valid_query_embs:
        sims = _cosine_similarity(q_emb, chunk_matrix)
        best = float(np.max(sims))
        relevance_scores.append(best)
        if best >= COVERAGE_FLOOR:
            covered += 1
    relevance = float(np.mean(relevance_scores)) if relevance_scores else 0.0
    coverage = covered / len(valid_query_embs) if valid_query_embs else 0.0

    # Diversity: 1 − mean upper-triangle pairwise cosine across final chunks.
    # High = varied passages; low = retrieval collapsed onto near-duplicates.
    diversity = _compute_diversity(chunk_matrix)

    # Per-chunk scores (max cosine to any query) — aligned to `chunks` order.
    all_query_matrix = np.vstack(valid_query_embs)
    per_chunk_scores: list[float] = []
    for emb in chunk_embeddings:
        if emb is not None and emb.shape[0] == chunk_dim:
            sims = _cosine_similarity(emb, all_query_matrix)
            per_chunk_scores.append(float(np.max(sims)))
        else:
            per_chunk_scores.append(0.0)

    chunk_details = [
        {"score": round(s, 4), "source": src}
        for s, src in zip(per_chunk_scores, chunk_sources)
    ]

    return RetrievalMetrics(
        relevance=max(0.0, min(1.0, relevance)),
        coverage=max(0.0, min(1.0, coverage)),
        diversity=max(0.0, min(1.0, diversity)),
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


def _compute_diversity(chunk_matrix: np.ndarray) -> float:
    """1 − mean upper-triangle pairwise cosine across the retrieved chunks.

    High = varied passages; low = retrieval collapsed onto near-duplicates.
    A single chunk has no pairs to compare → diversity is undefined; return 0.0
    (one chunk is, by definition, not a diverse result set).
    """
    n = chunk_matrix.shape[0]
    if n < 2:
        return 0.0
    # Row-normalize, then the Gram matrix is all pairwise cosines.
    norms = np.linalg.norm(chunk_matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normed = chunk_matrix / norms
    gram = normed @ normed.T
    iu = np.triu_indices(n, k=1)
    mean_sim = float(np.mean(gram[iu]))
    return max(0.0, min(1.0, 1.0 - mean_sim))
