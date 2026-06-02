"""Phase 3 RAG retrieval — per-weak-point queries + RRF fusion + semantic dedup.

Replaces the legacy "concat 5 weak_points into one query + generic topic
query + c[:100] prefix dedup" pipeline. The legacy approach diluted
semantic signal (you can't ask a vector DB about 5 things at once and
expect strong ranking) and the prefix dedup missed paraphrased near-duplicates.

This module:
1. Issues one RAG query per top-N weak_point, concurrently.
2. Fuses the ranked results via Reciprocal Rank Fusion (RRF) — a robust
   parameter-light merge that doesn't require score calibration.
3. Drops near-duplicate chunks via embedding cosine ≥ ``SIMILARITY_THRESHOLD``.
4. Caches every embedding hop via Redis so repeat queries hit memory.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Iterable

import numpy as np

from backend.indexer import safe_retrieve_topic_context
from backend.redis_cache import get_cache
from backend.vector_memory import _cosine_similarity

logger = logging.getLogger("uvicorn")


SIMILARITY_THRESHOLD = 0.85   # cosine — > this means "the same chunk paraphrased"
RRF_K = 60                    # standard RRF smoothing constant
PER_QUERY_TOP_K = 5
FINAL_TOP_N = 10              # post-fusion, pre-context-cap

# Concurrency cap for embedding requests. The retrieval fan-out and the dedup
# phase both hit the SAME embedding key, so they share this bound: 5 simultaneous
# requests tripped DashScope's per-key concurrency, making every request stall to
# its full timeout. 2 stays under the throttle while still ~2.5× faster than
# serial. The two phases run sequentially, so the effective bound is never doubled.
_EMBED_CONCURRENCY = 2


@dataclass
class RetrievalStats:
    """Reportable metrics for the timeline detail."""
    queries: int
    raw_chunks: int
    fused_chunks: int
    final_chunks: int
    embed_cache_hits: int
    embed_cache_misses: int
    reranker_applied: bool = False


async def retrieve_for_drill(
    topic: str,
    user_id: str,
    weak_points: list[str],
    fallback_query: str,
    *,
    per_query_top_k: int = PER_QUERY_TOP_K,
    final_top_n: int = FINAL_TOP_N,
    timeout: float = 45.0,
) -> tuple[list[str], RetrievalStats]:
    """Run the Phase 3 retrieval pipeline.

    Args:
        topic: topic key (e.g. "python")
        user_id: scopes the index cache
        weak_points: top-N weak_point text strings (caller already ranked them)
        fallback_query: a generic-topic query used when weak_points is empty
            or as the "exploration" companion query

    Returns:
        (chunks, stats) where chunks is the final fused+deduped list and
        stats summarizes what happened (for the timeline UI).
    """
    queries: list[str] = list(weak_points[:5])  # cap, prevent fanout abuse
    if not queries:
        queries.append(fallback_query)
    elif len(queries) < 3:
        # Always include the fallback so we don't over-fit to weak_points
        # when the user has only 1-2 logged.
        queries.append(fallback_query)

    # Fan out — LlamaIndex retrieval is sync, but safe_retrieve_topic_context
    # already wraps it in asyncio.to_thread + timeout.
    # Use return_exceptions so a single timeout/error doesn't void the whole
    # batch — we still want partial RAG context.
    # Cap concurrent retrieval queries — each query drives an embedding request
    # against the same key (see _EMBED_CONCURRENCY).
    sem = asyncio.Semaphore(_EMBED_CONCURRENCY)

    async def _bounded(query: str):
        async with sem:
            return await safe_retrieve_topic_context(
                topic, query, user_id, top_k=per_query_top_k, timeout=timeout
            )

    raw_results = await asyncio.gather(
        *[_bounded(q) for q in queries],
        return_exceptions=True,
    )

    per_query_results: list[list[str]] = []
    for q, r in zip(queries, raw_results):
        if isinstance(r, Exception):
            logger.warning("RAG sub-query %r failed: %s", q[:60], r)
            per_query_results.append([])
        else:
            per_query_results.append(r)

    raw_count = sum(len(r) for r in per_query_results)
    fused_ranked = _reciprocal_rank_fusion(per_query_results, k=RRF_K)
    chunks_post_fusion = [c for c, _score in fused_ranked]

    # Semantic dedup using cached embeddings.
    deduped, hits, misses = await _semantic_dedup(chunks_post_fusion)

    # Cross-Encoder reranking (skipped if not configured). Rerank against the
    # user's actual weak_points — the same intent that drove retrieval — NOT the
    # generic fallback_query. Reranking by generic topic relevance would bury the
    # weak-point-precise chunks RRF surfaced. Fall back to the generic query only
    # when no weak_points were logged.
    rerank_query = " ".join(weak_points[:5]).strip() or fallback_query
    reranked, reranker_applied = await _rerank_if_available(rerank_query, deduped)

    final = reranked[:final_top_n]
    stats = RetrievalStats(
        queries=len(queries),
        raw_chunks=raw_count,
        fused_chunks=len(chunks_post_fusion),
        final_chunks=len(final),
        embed_cache_hits=hits,
        embed_cache_misses=misses,
        reranker_applied=reranker_applied,
    )
    return final, stats


# ── RRF ──

def _reciprocal_rank_fusion(rankings: Iterable[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Standard RRF: score(c) = Σ 1 / (k + rank_i(c)).

    Lower rank = higher in the original list = larger score contribution.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            scores[chunk] = scores.get(chunk, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


# ── Semantic dedup ──

async def _semantic_dedup(chunks: list[str]) -> tuple[list[str], int, int]:
    """Drop chunks whose cosine to a kept chunk ≥ SIMILARITY_THRESHOLD.

    Returns (kept_chunks, cache_hits, cache_misses).
    """
    if not chunks:
        return [], 0, 0

    embeddings, hits, misses = await _embed_many(chunks)
    # Some embeddings may have failed (None) — keep those chunks but don't
    # let them participate in the cosine check; they go through verbatim.
    kept: list[str] = []
    kept_matrix: np.ndarray | None = None  # (k, D) of kept embeddings, grown incrementally

    for chunk, emb in zip(chunks, embeddings):
        if emb is None:
            kept.append(chunk)
            continue
        if kept_matrix is None:
            kept.append(chunk)
            kept_matrix = emb[np.newaxis, :]
            continue
        sims = _cosine_similarity(emb, kept_matrix)
        if float(np.max(sims)) >= SIMILARITY_THRESHOLD:
            continue   # near-duplicate; skip
        kept.append(chunk)
        kept_matrix = np.vstack((kept_matrix, emb))

    return kept, hits, misses


# ── Embedding with cache ──

async def _embed_many(texts: list[str]) -> tuple[list[np.ndarray | None], int, int]:
    """Embed a list of texts, hitting the Redis/LRU cache first.

    Cache miss → call the LlamaIndex embedding model in a thread.
    Failures degrade to ``None`` for that slot — never raise.
    """
    cache = get_cache()

    # Probe the cache for all texts in a single thread hop: keeps a slow Redis
    # GET (socket_timeout up to 3s) off the event loop, and for the in-memory
    # LRU it's one context switch instead of N synchronous calls.
    def _probe_cache() -> tuple[list[np.ndarray | None], int, list[tuple[int, str]]]:
        probed: list[np.ndarray | None] = [None] * len(texts)
        hit = 0
        miss: list[tuple[int, str]] = []
        for idx, text in enumerate(texts):
            cached = cache.get_embedding(text)
            if cached is not None:
                probed[idx] = cached
                hit += 1
            else:
                miss.append((idx, text))
        return probed, hit, miss

    out, hits, misses_to_compute = await asyncio.to_thread(_probe_cache)

    if not misses_to_compute:
        return out, hits, 0

    try:
        from backend.llm_provider import get_embedding
        embed_model = get_embedding()
    except Exception as exc:
        logger.warning("RAG dedup: embedding backend unavailable (%s); skipping cosine dedup", exc)
        return out, hits, len(misses_to_compute)

    # Cap concurrency to the same bound as the retrieval fan-out — both hit the
    # SAME embedding key, and an unbounded gather (one task per miss, easily 20+
    # on a cold cache) tripped the per-key throttle, stalling every request to
    # its timeout and silently voiding dedup.
    sem = asyncio.Semaphore(_EMBED_CONCURRENCY)

    def _embed_and_cache(text: str) -> np.ndarray:
        # Both the sync LlamaIndex embed call and the cache write run in the
        # worker thread, so neither blocks the event loop.
        vec = embed_model.get_text_embedding(text)
        arr = np.asarray(vec, dtype=np.float32)
        cache.set_embedding(text, arr)
        return arr

    async def _one(text: str) -> np.ndarray | None:
        async with sem:
            try:
                return await asyncio.to_thread(_embed_and_cache, text)
            except Exception as exc:
                logger.warning("RAG dedup: embed failed for text=%r... (%s)", text[:60], exc)
                return None

    computed = await asyncio.gather(*[_one(text) for _, text in misses_to_compute])
    for (idx, _text), arr in zip(misses_to_compute, computed):
        out[idx] = arr

    return out, hits, len(misses_to_compute)


# ── Reranker ──

async def _rerank_if_available(query: str, chunks: list[str]) -> tuple[list[str], bool]:
    """Apply Cross-Encoder reranking if configured, otherwise pass through.

    rerank() already returns (chunks, False) when no reranker is configured, so
    we don't pre-check the config here — doing so would call get_channel() twice
    and rotate the channel's key index an extra time on every drill.
    """
    if len(chunks) <= 1:
        return chunks, False
    try:
        from backend.reranker import rerank
        return await rerank(query, chunks, top_n=len(chunks))
    except Exception as e:
        logger.warning("Reranker unavailable, skipping: %s", e)
        return chunks, False
