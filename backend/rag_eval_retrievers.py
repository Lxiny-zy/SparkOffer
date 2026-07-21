"""Evaluation adapters for atomic and production-shaped RAG retrieval."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Literal

from backend.indexer import (
    ChunkWithMeta,
    IndexNotReady,
    async_retrieve_topic_context_with_scores,
)
from backend.rag_ids import stable_chunk_id

EvalRetrievalMode = Literal["atomic_dense", "production_replay"]
VALID_RETRIEVAL_MODES = {"atomic_dense", "production_replay"}
RETRIEVAL_CONFIG_KEYS = (
    "per_query_top_k",
    "final_top_n",
    "embed_concurrency",
    "dedup_threshold",
    "end_to_end_timeout",
    "per_query_timeout",
    "reranker_read_timeout",
)


def get_evaluation_retrieval_config() -> dict[str, float | int]:
    """Resolve one immutable retrieval snapshot for a complete eval run."""
    from backend.ai_config import get_retrieval_setting

    return {key: get_retrieval_setting(key) for key in RETRIEVAL_CONFIG_KEYS}


@dataclass
class EvalRetrievalOutcome:
    mode: str
    status: str
    chunks: list[ChunkWithMeta] = field(default_factory=list)
    latency_ms: float = 0.0
    error_code: str = ""
    error: str = ""
    trace: dict = field(default_factory=dict)

    @property
    def measured(self) -> bool:
        return self.status in {"ok", "empty", "degraded"}

    def candidates(self, cutoff: int | None = None) -> list[dict]:
        chunks = self.chunks if cutoff is None else self.chunks[:cutoff]
        return [
            {
                "rank": rank,
                "node_id": c.node_id or stable_chunk_id(
                    c.content, c.source_file, c.header_path,
                ),
                "source_file": c.source_file,
                "header_path": c.header_path,
                "score": round(float(c.score), 6),
                "preview": (c.content or "")[:240],
            }
            for rank, c in enumerate(chunks, start=1)
        ]


def _error_text(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:300]


async def retrieve_for_evaluation(
    *,
    topic: str,
    user_id: str,
    queries: list[str],
    fallback_query: str,
    mode: EvalRetrievalMode,
    k: int,
    retrieval_config: dict[str, float | int] | None = None,
) -> EvalRetrievalOutcome:
    """Retrieve one atomic query or one production weak-point bundle.

    Evaluation never builds an index on the request path. Infrastructure
    failures remain explicit outcomes rather than being converted to quality
    zeros, while a successful retrieval with no matches is the measurable
    ``empty`` state.
    """
    started = time.perf_counter()
    if mode not in VALID_RETRIEVAL_MODES:
        raise ValueError(f"Unknown retrieval mode: {mode}")

    if mode == "atomic_dense":
        if len(queries) != 1:
            raise ValueError("atomic_dense evaluates exactly one query at a time")
        config = retrieval_config or get_evaluation_retrieval_config()
        per_query_timeout = config["per_query_timeout"]
        trace_base = {
            "queries": list(queries),
            "retrieval_config": {"per_query_timeout": per_query_timeout},
        }
        try:
            chunks = await async_retrieve_topic_context_with_scores(
                topic,
                queries[0],
                user_id,
                top_k=k,
                timeout=per_query_timeout,
                build_if_missing=False,
            )
        except IndexNotReady as exc:
            return EvalRetrievalOutcome(
                mode=mode,
                status="index_not_ready",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                error_code="index_not_ready",
                error=_error_text(exc),
                trace=trace_base,
            )
        except asyncio.TimeoutError as exc:
            return EvalRetrievalOutcome(
                mode=mode,
                status="timeout",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                error_code="timeout",
                error=_error_text(exc),
                trace=trace_base,
            )
        except Exception as exc:
            return EvalRetrievalOutcome(
                mode=mode,
                status="error",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                error_code=type(exc).__name__,
                error=_error_text(exc),
                trace=trace_base,
            )
        return EvalRetrievalOutcome(
            mode=mode,
            status="ok" if chunks else "empty",
            chunks=chunks,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            trace={
                **trace_base,
                "final_chunks": len(chunks),
                "candidates": [
                    {
                        "rank": rank,
                        "node_id": c.node_id,
                        "source_file": c.source_file,
                        "header_path": c.header_path,
                        "dense_score": round(float(c.score), 6),
                    }
                    for rank, c in enumerate(chunks, start=1)
                ],
            },
        )

    from backend.graphs.rag_retrieval import retrieve_for_drill

    # Resolve the hot-reloadable knobs once per bundle. A settings edit halfway
    # through a run must not silently produce a mixed configuration baseline.
    retrieval_config = retrieval_config or get_evaluation_retrieval_config()

    try:
        texts, stats = await asyncio.wait_for(
            retrieve_for_drill(
                topic=topic,
                user_id=user_id,
                weak_points=queries,
                fallback_query=fallback_query,
                per_query_top_k=retrieval_config["per_query_top_k"],
                final_top_n=retrieval_config["final_top_n"],
                timeout=retrieval_config["per_query_timeout"],
                embed_concurrency=retrieval_config["embed_concurrency"],
                dedup_threshold=retrieval_config["dedup_threshold"],
                reranker_read_timeout=retrieval_config["reranker_read_timeout"],
                strict=True,
            ),
            timeout=retrieval_config["end_to_end_timeout"],
        )
    except asyncio.TimeoutError as exc:
        return EvalRetrievalOutcome(
            mode=mode,
            status="timeout",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            error_code="end_to_end_timeout",
            error=_error_text(exc),
        )
    except Exception as exc:
        code = "index_not_ready" if isinstance(exc, IndexNotReady) else type(exc).__name__
        return EvalRetrievalOutcome(
            mode=mode,
            status="index_not_ready" if isinstance(exc, IndexNotReady) else "error",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            error_code=code,
            error=_error_text(exc),
        )

    details = stats.final_chunk_details or []
    chunks: list[ChunkWithMeta] = []
    for idx, content in enumerate(texts):
        d = details[idx] if idx < len(details) else {}
        source_file = d.get("source_file", "")
        header_path = d.get("header_path", "")
        chunks.append(ChunkWithMeta(
            content=content,
            score=float(d.get("dense_score") or 0.0),
            source_file=source_file,
            header_path=header_path,
            node_id=d.get("node_id") or stable_chunk_id(
                content, source_file, header_path,
            ),
        ))

    failure_codes = stats.failure_codes or []
    if stats.failed_queries >= stats.queries:
        unique_codes = set(failure_codes)
        if unique_codes == {"index_not_ready"}:
            status = "index_not_ready"
            error_code = "index_not_ready"
        elif unique_codes == {"timeout"}:
            status = "timeout"
            error_code = "timeout"
        elif len(unique_codes) == 1:
            status = "error"
            error_code = next(iter(unique_codes))
        else:
            status = "error"
            error_code = "mixed_error" if unique_codes else "all_dense_queries_failed"
    elif (
        stats.failed_queries
        or stats.reranker_status == "degraded"
        or stats.dedup_embedding_failures
    ):
        status = "degraded"
        if stats.failed_queries:
            error_code = "partial_retrieval_failure"
        elif stats.reranker_status == "degraded":
            error_code = "reranker_degraded"
        else:
            error_code = "dedup_embedding_degraded"
    else:
        status = "ok" if chunks else "empty"
        error_code = ""

    return EvalRetrievalOutcome(
        mode=mode,
        status=status,
        chunks=chunks,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        error_code=error_code,
        trace={
            "queries": list(queries),
            "query_count": stats.queries,
            "failed_queries": stats.failed_queries,
            "failure_codes": failure_codes,
            "failure_details": stats.failure_details or [],
            "raw_chunks": stats.raw_chunks,
            "fused_chunks": stats.fused_chunks,
            "final_chunks": stats.final_chunks,
            "embed_cache_hits": stats.embed_cache_hits,
            "embed_cache_misses": stats.embed_cache_misses,
            "dedup_embedding_failures": stats.dedup_embedding_failures,
            "reranker_status": stats.reranker_status,
            "stage_latency_ms": stats.stage_latency_ms or {},
            "retrieval_config": retrieval_config,
            "candidates": details,
        },
    )
