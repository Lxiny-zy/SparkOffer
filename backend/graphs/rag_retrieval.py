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
import time
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from backend.indexer import safe_retrieve_topic_context, safe_retrieve_topic_context_with_scores, ChunkWithMeta
from backend.redis_cache import get_cache
from backend.vector_store.base import _cosine_similarity

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
    reranker_status: str = "off"   # "applied" | "degraded" | "off"
    rag_metrics: dict | None = None  # serialized RetrievalMetrics from rag_metrics.py
    failed_queries: int = 0
    failure_codes: list[str] = field(default_factory=list)
    failure_details: list[dict] = field(default_factory=list)
    dedup_embedding_failures: int = 0
    stage_latency_ms: dict[str, float] | None = None
    final_chunk_details: list[dict] | None = None


def _retrieval_failure_code(exc: BaseException) -> str:
    """Normalize sub-query failures for evaluator diagnostics."""
    from backend.indexer import IndexNotReady

    if isinstance(exc, IndexNotReady):
        return "index_not_ready"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return type(exc).__name__ or "error"


async def retrieve_for_drill(
    topic: str,
    user_id: str,
    weak_points: list[str],
    fallback_query: str,
    *,
    per_query_top_k: int | None = None,
    final_top_n: int | None = None,
    timeout: float | None = None,
    embed_concurrency: int | None = None,
    dedup_threshold: float | None = None,
    reranker_read_timeout: float | None = None,
    strict: bool = False,
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
    # Resolve retrieval knobs at call time so a settings save hot-applies. An
    # explicit arg (e.g. from an eval harness) still wins over the configured value.
    from backend.ai_config import get_retrieval_setting
    per_query_top_k = (
        per_query_top_k if per_query_top_k is not None
        else get_retrieval_setting("per_query_top_k")
    )
    final_top_n = (
        final_top_n if final_top_n is not None
        else get_retrieval_setting("final_top_n")
    )
    timeout = timeout if timeout is not None else get_retrieval_setting("per_query_timeout")
    embed_concurrency = (
        embed_concurrency if embed_concurrency is not None
        else get_retrieval_setting("embed_concurrency")
    )
    dedup_threshold = (
        dedup_threshold if dedup_threshold is not None
        else get_retrieval_setting("dedup_threshold")
    )
    reranker_read_timeout = (
        reranker_read_timeout if reranker_read_timeout is not None
        else get_retrieval_setting("reranker_read_timeout")
    )

    queries: list[str] = list(weak_points[:5])  # cap, prevent fanout abuse
    if not queries:
        queries.append(fallback_query)
    elif len(queries) < 3:
        queries.append(fallback_query)

    started = time.perf_counter()
    stage_started = started
    stage_latency_ms: dict[str, float] = {}
    sem = asyncio.Semaphore(embed_concurrency)

    async def _bounded(query: str):
        async with sem:
            if strict:
                from backend.indexer import async_retrieve_topic_context_with_scores
                return await async_retrieve_topic_context_with_scores(
                    topic, query, user_id, top_k=per_query_top_k,
                    timeout=timeout, build_if_missing=False,
                )
            return await safe_retrieve_topic_context_with_scores(
                topic, query, user_id, top_k=per_query_top_k, timeout=timeout,
                build_if_missing=False,
            )

    raw_results = await asyncio.gather(
        *[_bounded(q) for q in queries],
        return_exceptions=True,
    )
    stage_latency_ms["dense_fanout"] = round((time.perf_counter() - stage_started) * 1000, 2)

    per_query_chunks: list[list[ChunkWithMeta]] = []
    per_query_texts: list[list[str]] = []
    failure_codes: list[str] = []
    failure_details: list[dict] = []
    for query_index, (q, r) in enumerate(zip(queries, raw_results)):
        if isinstance(r, BaseException):
            code = _retrieval_failure_code(r)
            failure_codes.append(code)
            failure_details.append({
                "query_index": query_index,
                "query": q[:120],
                "code": code,
                "error": str(r).replace("\n", " ")[:300],
            })
            logger.warning("RAG sub-query %r failed: %s", q[:60], r)
            per_query_chunks.append([])
            per_query_texts.append([])
        else:
            per_query_chunks.append(r)
            per_query_texts.append([c.content for c in r])

    failed_queries = len(failure_details)

    raw_count = sum(len(r) for r in per_query_texts)

    # RRF fusion on text content; build a lookup for metadata
    chunk_meta_map: dict[str, ChunkWithMeta] = {}
    for chunk_list in per_query_chunks:
        for cm in chunk_list:
            if cm.content not in chunk_meta_map:
                chunk_meta_map[cm.content] = cm

    stage_started = time.perf_counter()
    fused_ranked = _reciprocal_rank_fusion(per_query_texts, k=RRF_K)
    fused_score_map = dict(fused_ranked)
    chunks_post_fusion = [c for c, _score in fused_ranked]
    stage_latency_ms["rrf"] = round((time.perf_counter() - stage_started) * 1000, 2)

    # Semantic dedup — now also returns embeddings for metrics computation.
    stage_started = time.perf_counter()
    deduped, hits, misses, dedup_embeddings = await _semantic_dedup(
        chunks_post_fusion,
        threshold=dedup_threshold,
        embed_concurrency=embed_concurrency,
    )
    dedup_embedding_failures = sum(emb is None for emb in dedup_embeddings)
    stage_latency_ms["dedup"] = round((time.perf_counter() - stage_started) * 1000, 2)

    rerank_query = " ".join(weak_points[:5]).strip() or fallback_query
    stage_started = time.perf_counter()
    reranked, reranker_status = await _rerank_if_available(
        rerank_query, deduped, read_timeout=reranker_read_timeout,
    )
    stage_latency_ms["rerank"] = round((time.perf_counter() - stage_started) * 1000, 2)

    final = reranked[:final_top_n]

    # Build aligned metadata and embeddings for final chunks
    final_sources: list[str] = []
    final_embeddings: list = []
    final_chunk_details: list[dict] = []
    dedup_emb_map = {text: emb for text, emb in zip(deduped, dedup_embeddings)}
    from backend.rag_ids import stable_chunk_id
    for rank, chunk_text in enumerate(final, start=1):
        cm = chunk_meta_map.get(chunk_text)
        src = f"{cm.source_file}" if cm else ""
        if cm and cm.header_path:
            src = f"{src} > {cm.header_path}" if src else cm.header_path
        final_sources.append(src)
        final_embeddings.append(dedup_emb_map.get(chunk_text))
        source_file = cm.source_file if cm else ""
        header_path = cm.header_path if cm else ""
        final_chunk_details.append({
            "rank": rank,
            "node_id": cm.node_id if cm and cm.node_id else stable_chunk_id(
                chunk_text, source_file, header_path,
            ),
            "source_file": source_file,
            "header_path": header_path,
            "dense_score": round(float(cm.score), 6) if cm else None,
            "rrf_score": round(float(fused_score_map.get(chunk_text, 0.0)), 8),
        })

    # Compute RAG retrieval quality metrics (embedding-only, zero LLM cost).
    # compute_retrieval_metrics returns None when it can't measure (degraded
    # embeddings, dim mismatch) — keep rag_metrics None in that case so we don't
    # persist a fake all-zeros record that looks like collapsed retrieval.
    rag_metrics_dict = None
    try:
        from backend.rag_metrics import compute_retrieval_metrics
        metrics = await compute_retrieval_metrics(
            query_texts=queries,
            chunks=final,
            chunk_embeddings=final_embeddings,
            chunk_sources=final_sources,
        )
        rag_metrics_dict = metrics.to_dict() if metrics is not None else None
    except Exception as exc:
        logger.warning("RAG metrics computation failed: %s", exc)

    stats = RetrievalStats(
        queries=len(queries),
        raw_chunks=raw_count,
        fused_chunks=len(chunks_post_fusion),
        final_chunks=len(final),
        embed_cache_hits=hits,
        embed_cache_misses=misses,
        reranker_status=reranker_status,
        rag_metrics=rag_metrics_dict,
        failed_queries=failed_queries,
        failure_codes=failure_codes,
        failure_details=failure_details,
        dedup_embedding_failures=dedup_embedding_failures,
        stage_latency_ms={
            **stage_latency_ms,
            "total": round((time.perf_counter() - started) * 1000, 2),
        },
        final_chunk_details=final_chunk_details,
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

async def _semantic_dedup(
    chunks: list[str],
    *,
    threshold: float | None = None,
    embed_concurrency: int | None = None,
) -> tuple[list[str], int, int, list]:
    """Drop chunks whose cosine to a kept chunk ≥ SIMILARITY_THRESHOLD.

    Returns (kept_chunks, cache_hits, cache_misses, kept_embeddings).
    """
    if not chunks:
        return [], 0, 0, []

    from backend.ai_config import get_retrieval_setting
    threshold = (
        threshold if threshold is not None
        else get_retrieval_setting("dedup_threshold")
    )
    embeddings, hits, misses = await _embed_many(
        chunks, embed_concurrency=embed_concurrency,
    )
    kept: list[str] = []
    kept_embs: list = []
    kept_matrix: np.ndarray | None = None

    for chunk, emb in zip(chunks, embeddings):
        if emb is None:
            kept.append(chunk)
            kept_embs.append(None)
            continue
        if kept_matrix is None:
            kept.append(chunk)
            kept_embs.append(emb)
            kept_matrix = emb[np.newaxis, :]
            continue
        sims = _cosine_similarity(emb, kept_matrix)
        if float(np.max(sims)) >= threshold:
            continue
        kept.append(chunk)
        kept_embs.append(emb)
        kept_matrix = np.vstack((kept_matrix, emb))

    return kept, hits, misses, kept_embs


# ── Embedding with cache ──

async def _embed_many(
    texts: list[str], *, embed_concurrency: int | None = None,
) -> tuple[list[np.ndarray | None], int, int]:
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
    from backend.ai_config import get_retrieval_setting
    concurrency = (
        embed_concurrency if embed_concurrency is not None
        else get_retrieval_setting("embed_concurrency")
    )
    sem = asyncio.Semaphore(concurrency)

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

async def _rerank_if_available(
    query: str, chunks: list[str], *, read_timeout: float | None = None,
) -> tuple[list[str], str]:
    """Apply Cross-Encoder reranking if configured, otherwise pass through.

    Returns (chunks, status): "applied" | "degraded" | "off" — surfaced in the
    pipeline timeline so the user can see whether reranking actually ran.

    rerank() already returns (chunks, "off") when no reranker is configured, so
    we don't pre-check the config here — doing so would call get_channel() twice
    and rotate the channel's key index an extra time on every drill.
    """
    if len(chunks) <= 1:
        return chunks, "off"
    try:
        from backend.reranker import rerank
        return await rerank(
            query, chunks, top_n=len(chunks), read_timeout=read_timeout,
        )
    except Exception as e:
        logger.warning("Reranker unavailable, skipping: %s", e)
        return chunks, "degraded"
