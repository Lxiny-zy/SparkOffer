"""Frozen, reproducible retrieval benchmark for the project's RAG pipeline.

This complements ``backend.rag_eval``:

* ``frozen_retrieval`` uses the versioned keyword-qrel suite in
  ``data/eval/rag_queries.json``. It is deterministic, inexpensive, and suited
  to before/after regression checks.
* ``synthetic_e2e`` (the existing engine) generates questions and uses LLM
  judges. It explores answer quality, but is inherently less reproducible.

The benchmark can exercise either the atomic dense retriever or a production-
shaped bundle that passes through multi-query retrieval, RRF, semantic dedup,
and the configured reranker.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.eval.rag_manifest import (
    METRIC_SEMANTICS_VERSION,
    build_run_manifest,
    finalize_comparison_signature,
    hash_file,
    hash_topic_corpus,
    sha256_json,
)
from backend.rag_eval_retrievers import (
    EvalRetrievalOutcome,
    get_evaluation_retrieval_config,
    retrieve_for_evaluation,
)

logger = logging.getLogger("uvicorn")

_RUNTIME_DATASET_PATH = settings.base_dir / "data" / "eval" / "rag_queries.json"
_BUNDLED_DATASET_PATH = Path(__file__).resolve().parent / "data" / "rag_queries.json"
# Compose bind-mounts /app/data and therefore hides files copied into that image
# path. Prefer the mutable host dataset when present, otherwise use the immutable
# image copy under backend/eval/data.
DEFAULT_DATASET_PATH = (
    _RUNTIME_DATASET_PATH if _RUNTIME_DATASET_PATH.exists()
    else _BUNDLED_DATASET_PATH
)
DATASET_ID = "rag-keyword-regression"
PRODUCTION_BUNDLE_SIZE = 5


@dataclass(frozen=True)
class FrozenCase:
    case_id: str
    topic: str
    question: str
    must_include_any: tuple[str, ...]
    expected_keywords: tuple[str, ...]
    difficulty: str = "unspecified"
    query_type: str = "unspecified"


def load_frozen_cases(
    topic: str,
    limit: int,
    dataset_path: Path = DEFAULT_DATASET_PATH,
) -> tuple[list[FrozenCase], str, str]:
    """Load a stable file-order slice and return cases, version, file hash."""
    raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    version = str(raw.get("version", "unknown"))
    cases: list[FrozenCase] = []
    seen: set[str] = set()
    for item in raw.get("queries", []):
        if item.get("topic") != topic:
            continue
        case_id = str(item.get("id") or "").strip()
        question = str(item.get("query") or "").strip()
        if not case_id or not question or case_id in seen:
            continue
        seen.add(case_id)
        cases.append(FrozenCase(
            case_id=case_id,
            topic=topic,
            question=question,
            must_include_any=tuple(str(x) for x in item.get("must_include_any", []) if str(x)),
            expected_keywords=tuple(str(x) for x in item.get("expected_keywords", []) if str(x)),
            difficulty=str(item.get("difficulty") or "unspecified"),
            query_type=str(item.get("type") or "unspecified"),
        ))
    if limit > 0:
        cases = cases[:limit]
    return cases, version, hash_file(dataset_path)


def _chunk_hits(content: str, keywords: tuple[str, ...]) -> bool:
    low = (content or "").lower()
    return any(keyword.lower() in low for keyword in keywords if keyword)


def _ndcg(flags: list[bool]) -> float:
    if not flags or not any(flags):
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, relevant in enumerate(flags, start=1)
        if relevant
    )
    ideal_relevant = sum(flags)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_relevant + 1))
    return dcg / idcg if idcg else 0.0


def score_frozen_case(
    case: FrozenCase,
    outcome: EvalRetrievalOutcome,
    k: int,
    *,
    bundle_id: str,
) -> dict[str, Any]:
    """Score one target against its atomic or shared production context."""
    chunks = outcome.chunks[:k] if outcome.measured else []
    flags = [_chunk_hits(chunk.content, case.must_include_any) for chunk in chunks]
    first_rank = next((rank for rank, hit in enumerate(flags, start=1) if hit), None)
    union = "\n".join(chunk.content for chunk in chunks).lower()
    expected = case.expected_keywords
    keyword_recall = (
        sum(1 for keyword in expected if keyword.lower() in union) / len(expected)
        if expected else 0.0
    )
    return {
        "id": case.case_id,
        "topic": case.topic,
        "difficulty": case.difficulty,
        "type": case.query_type,
        "question": case.question,
        "bundle_id": bundle_id,
        "outcome": outcome.status,
        "error_code": outcome.error_code,
        "error": outcome.error,
        "hit_at_k": 1.0 if first_rank is not None else 0.0,
        "rank": first_rank,
        "mrr": 1.0 / first_rank if first_rank else 0.0,
        "ndcg_at_k": _ndcg(flags),
        "context_precision": sum(flags) / len(chunks) if chunks else 0.0,
        "context_recall": keyword_recall,
        "n_chunks": len(chunks),
        "n_relevant_chunks": sum(flags),
        "latency_ms": outcome.latency_ms,
    }


def _mean(rows: list[dict], key: str) -> float:
    return sum(float(row.get(key) or 0.0) for row in rows) / len(rows) if rows else 0.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def aggregate_benchmark_results(
    rows: list[dict],
    attempt_latencies: list[float],
) -> dict[str, Any]:
    """Aggregate with infrastructure failures retained as zero-quality cases."""
    measured = sum(1 for row in rows if row.get("outcome") in {"ok", "empty", "degraded"})
    degraded = sum(1 for row in rows if row.get("outcome") == "degraded")
    fully_healthy = sum(1 for row in rows if row.get("outcome") in {"ok", "empty"})
    success_rate = measured / len(rows) if rows else 0.0
    valid = bool(rows) and success_rate >= 0.95
    return {
        "hit_at_k": round(_mean(rows, "hit_at_k"), 4),
        "hit_at_k_strict": None,
        "mrr": round(_mean(rows, "mrr"), 4),
        "ndcg_at_k": round(_mean(rows, "ndcg_at_k"), 4),
        "context_precision": round(_mean(rows, "context_precision"), 4),
        "context_recall": round(_mean(rows, "context_recall"), 4),
        "faithfulness": None,
        "answer_relevancy": None,
        "answer_correctness": None,
        "n_questions": len(rows),
        "evaluated_questions": measured,
        "error_count": len(rows) - measured,
        "success_rate": round(success_rate, 4),
        "degraded_count": degraded,
        "fully_healthy_rate": round(fully_healthy / len(rows), 4) if rows else 0.0,
        "latency_p50_ms": round(_percentile(attempt_latencies, 0.50), 2),
        "latency_p95_ms": round(_percentile(attempt_latencies, 0.95), 2),
        "valid": valid,
        "comparable": valid and degraded == 0,
    }


def _group_breakdown(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "unspecified"), []).append(row)
    return {
        label: {
            "n": len(group),
            "hit_at_k": round(_mean(group, "hit_at_k"), 4),
            "mrr": round(_mean(group, "mrr"), 4),
            "ndcg_at_k": round(_mean(group, "ndcg_at_k"), 4),
            "context_precision": round(_mean(group, "context_precision"), 4),
            "context_recall": round(_mean(group, "context_recall"), 4),
        }
        for label, group in groups.items()
    }


def _production_fallback(topic: str) -> str:
    path = settings.base_dir / "data" / "topic_subtopics.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        subtopics = data.get(topic) or []
        if subtopics:
            return " ".join(subtopics[:3])
    except Exception:
        pass
    return f"{topic} 核心知识点 面试常见问题"


async def _execute_cases(
    cases: list[FrozenCase],
    *,
    topic: str,
    user_id: str,
    retrieval_mode: str,
    k: int,
    fallback_query: str,
    retrieval_config: dict[str, float | int],
    job: dict,
) -> tuple[list[dict], list[dict], list[float]]:
    rows: list[dict] = []
    bundle_details: list[dict] = []
    latencies: list[float] = []

    if retrieval_mode == "production_replay":
        bundles = [cases[i:i + PRODUCTION_BUNDLE_SIZE] for i in range(0, len(cases), PRODUCTION_BUNDLE_SIZE)]
        for bundle_index, bundle in enumerate(bundles, start=1):
            bundle_id = f"bundle-{bundle_index:03d}"
            outcome = await retrieve_for_evaluation(
                topic=topic,
                user_id=user_id,
                queries=[case.question for case in bundle],
                fallback_query=fallback_query,
                mode="production_replay",
                k=k,
                retrieval_config=retrieval_config,
            )
            latencies.append(outcome.latency_ms)
            rows.extend(
                score_frozen_case(case, outcome, k, bundle_id=bundle_id)
                for case in bundle
            )
            bundle_details.append({
                "bundle_id": bundle_id,
                "case_ids": [case.case_id for case in bundle],
                "status": outcome.status,
                "error_code": outcome.error_code,
                "error": outcome.error,
                "latency_ms": outcome.latency_ms,
                "trace": outcome.trace,
                "candidates": outcome.candidates(k),
            })
            job["done"] = min(len(cases), job.get("done", 0) + len(bundle))
            job["updated_at"] = time.time()
        return rows, bundle_details, latencies

    sem = asyncio.Semaphore(2)

    async def _one(index: int, case: FrozenCase) -> tuple[dict, dict, float]:
        async with sem:
            outcome = await retrieve_for_evaluation(
                topic=topic,
                user_id=user_id,
                queries=[case.question],
                fallback_query=fallback_query,
                mode="atomic_dense",
                k=k,
                retrieval_config=retrieval_config,
            )
        bundle_id = f"query-{index:03d}"
        row = score_frozen_case(case, outcome, k, bundle_id=bundle_id)
        detail = {
            "bundle_id": bundle_id,
            "case_ids": [case.case_id],
            "status": outcome.status,
            "error_code": outcome.error_code,
            "error": outcome.error,
            "latency_ms": outcome.latency_ms,
            "trace": outcome.trace,
            "candidates": outcome.candidates(k),
        }
        job["done"] = job.get("done", 0) + 1
        job["updated_at"] = time.time()
        return row, detail, outcome.latency_ms

    results = await asyncio.gather(*[
        _one(index, case) for index, case in enumerate(cases, start=1)
    ])
    for row, detail, latency in results:
        rows.append(row)
        bundle_details.append(detail)
        latencies.append(latency)
    rows.sort(key=lambda row: next(
        i for i, case in enumerate(cases) if case.case_id == row["id"]
    ))
    bundle_details.sort(key=lambda detail: detail["bundle_id"])
    return rows, bundle_details, latencies


async def run_frozen_benchmark(
    job: dict,
    topic: str,
    user_id: str,
    n: int,
    k: int,
    retrieval_mode: str,
    seed: int,
    *,
    persist: bool = True,
) -> None:
    """Background-job target shared by the API and Docker-friendly CLI."""
    manifest: dict = {}
    try:
        job.update({
            "status": "running",
            "phase": "loading_dataset",
            "updated_at": time.time(),
        })
        cases, dataset_version, dataset_hash = await asyncio.to_thread(
            load_frozen_cases, topic, n, DEFAULT_DATASET_PATH,
        )
        if not cases:
            raise ValueError(f"固定评测集中没有 topic={topic!r} 的 case")

        corpus_hash, corpus_file_count = await asyncio.to_thread(
            hash_topic_corpus, topic, user_id,
        )
        fallback_query = _production_fallback(topic)
        retrieval_config = get_evaluation_retrieval_config()
        protocol = {
            "case_selection": "stable_file_order_prefix",
            "bundle_size": PRODUCTION_BUNDLE_SIZE if retrieval_mode == "production_replay" else 1,
            "fallback_query_hash": sha256_json(fallback_query),
            "relevance_labels": "case_insensitive_keyword_substring",
        }
        manifest = build_run_manifest(
            eval_kind="frozen_retrieval",
            retrieval_mode=retrieval_mode,
            topic=topic,
            user_id=user_id,
            dataset_id=DATASET_ID,
            dataset_version=dataset_version,
            dataset_hash=dataset_hash,
            seed=seed,
            case_ids=[case.case_id for case in cases],
            corpus_hash=corpus_hash,
            corpus_file_count=corpus_file_count,
            k=k,
            judge_mode="none",
            protocol=protocol,
            retrieval_config_snapshot=retrieval_config,
        )
        initial_comparison_signature = manifest["comparison_signature"]
        job.update({
            "phase": "retrieving",
            "done": 0,
            "total": len(cases),
            "manifest": manifest,
            "updated_at": time.time(),
        })
        rows, bundles, latencies = await _execute_cases(
            cases,
            topic=topic,
            user_id=user_id,
            retrieval_mode=retrieval_mode,
            k=k,
            fallback_query=fallback_query,
            retrieval_config=retrieval_config,
            job=job,
        )

        job.update({"phase": "aggregating", "updated_at": time.time()})
        summary = aggregate_benchmark_results(rows, latencies)
        statuses = [str(row.get("outcome") or "error") for row in rows]
        if any(status not in {"ok", "empty", "degraded"} for status in statuses):
            execution_profile = "infrastructure_failure"
        elif any(status == "degraded" for status in statuses):
            execution_profile = "degraded"
        else:
            execution_profile = "healthy"
        post_corpus_hash, post_corpus_file_count = await asyncio.to_thread(
            hash_topic_corpus, topic, user_id,
        )
        post_manifest = build_run_manifest(
            eval_kind="frozen_retrieval",
            retrieval_mode=retrieval_mode,
            topic=topic,
            user_id=user_id,
            dataset_id=DATASET_ID,
            dataset_version=dataset_version,
            dataset_hash=dataset_hash,
            seed=seed,
            case_ids=[case.case_id for case in cases],
            corpus_hash=post_corpus_hash,
            corpus_file_count=post_corpus_file_count,
            k=k,
            judge_mode="none",
            protocol=protocol,
            retrieval_config_snapshot=retrieval_config,
        )
        state_stable = post_manifest["comparison_signature"] == initial_comparison_signature
        if not state_stable:
            execution_profile = "state_changed_during_run"
        manifest["post_run_comparison_signature"] = post_manifest["comparison_signature"]
        manifest["state_stable"] = state_stable
        manifest["observations"] = {
            "success_rate": summary["success_rate"],
            "fully_healthy_rate": summary["fully_healthy_rate"],
            "degraded_count": summary["degraded_count"],
        }
        finalize_comparison_signature(manifest, execution_profile=execution_profile)
        summary["comparable"] = bool(summary["valid"] and state_stable and execution_profile == "healthy")
        detail = {
            "metric_semantics_version": METRIC_SEMANTICS_VERSION,
            "eval_kind": "frozen_retrieval",
            "retrieval_mode": retrieval_mode,
            "dataset_id": DATASET_ID,
            "dataset_version": dataset_version,
            "dataset_hash": dataset_hash,
            "corpus_hash": corpus_hash,
            "k": k,
            "execution_profile": execution_profile,
            "state_stable": state_stable,
            "groups": {
                "difficulty": _group_breakdown(rows, "difficulty"),
                "type": _group_breakdown(rows, "type"),
            },
            "questions": rows,
            "bundles": bundles,
        }

        run_id = None
        if persist:
            from backend.storage.rag_eval_store import save_rag_eval_run
            run_id = await asyncio.to_thread(
                save_rag_eval_run,
                job_id=job["job_id"],
                user_id=user_id,
                topic=topic,
                scope=job.get("scope", "topic"),
                n_questions=len(cases),
                k=k,
                judge_mode="none",
                hit_at_k=summary["hit_at_k"],
                hit_at_k_strict=None,
                mrr=summary["mrr"],
                ndcg_at_k=summary["ndcg_at_k"],
                context_precision=summary["context_precision"],
                context_recall=summary["context_recall"],
                faithfulness=None,
                answer_relevancy=None,
                answer_correctness=None,
                success_rate=summary["success_rate"],
                latency_p50_ms=summary["latency_p50_ms"],
                latency_p95_ms=summary["latency_p95_ms"],
                eval_kind="frozen_retrieval",
                retrieval_mode=retrieval_mode,
                dataset_id=DATASET_ID,
                dataset_version=dataset_version,
                dataset_hash=dataset_hash,
                corpus_hash=corpus_hash,
                seed=seed,
                status="completed",
                error="",
                manifest=manifest,
                detail=detail,
            )

        job.update({
            "summary": summary,
            "detail": detail,
            "run_id": run_id,
            "status": "completed",
            "phase": "completed",
            "updated_at": time.time(),
        })
    except Exception as exc:
        logger.exception("frozen RAG benchmark failed")
        message = str(exc)[:300]
        job.update({
            "status": "failed",
            "phase": "failed",
            "error": message,
            "updated_at": time.time(),
        })
        if persist:
            try:
                from backend.storage.rag_eval_store import save_failed_rag_eval_run
                job["run_id"] = await asyncio.to_thread(
                    save_failed_rag_eval_run,
                    job_id=job["job_id"],
                    user_id=user_id,
                    topic=topic,
                    scope=job.get("scope", "topic"),
                    n_questions=int(job.get("total") or n),
                    k=k,
                    judge_mode="none",
                    eval_kind="frozen_retrieval",
                    retrieval_mode=retrieval_mode,
                    seed=seed,
                    error=message,
                    manifest=manifest,
                    detail={
                        "phase": job.get("phase", "failed"),
                        "done": job.get("done", 0),
                        "total": job.get("total", 0),
                    },
                )
            except Exception:
                logger.exception("failed to persist failed frozen RAG benchmark")


def _bootstrap() -> None:
    from backend.ai_config import init_config
    from backend.indexer import _init_llama_settings
    from backend.storage.database import init_all_tables

    init_config()
    init_all_tables()
    _init_llama_settings()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the frozen RAG retrieval benchmark")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument(
        "--mode", choices=("atomic_dense", "production_replay"),
        default="production_replay",
    )
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--n-questions", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    _bootstrap()
    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "scope": "topic",
        "status": "pending",
        "phase": "pending",
        "done": 0,
        "total": 0,
        "error": None,
    }
    asyncio.run(run_frozen_benchmark(
        job,
        args.topic,
        args.user_id,
        max(1, min(100, args.n_questions)),
        max(1, min(20, args.top_k)),
        args.mode,
        max(0, args.seed),
        persist=not args.no_persist,
    ))

    output = Path(args.output_json) if args.output_json else (
        settings.base_dir / "data" / "eval" / "reports" / f"rag_benchmark_{job_id}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": job.get("status"),
        "summary": job.get("summary"),
        "output": str(output),
    }, ensure_ascii=False, indent=2))
    comparable = bool((job.get("summary") or {}).get("comparable"))
    sys.exit(0 if job.get("status") == "completed" and comparable else 3)


if __name__ == "__main__":
    main()
