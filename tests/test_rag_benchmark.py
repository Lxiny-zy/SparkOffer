"""Deterministic unit tests for the frozen RAG retrieval benchmark.

These tests exercise only pure scoring and serialization helpers. They must not
reach an LLM, embedding provider, or vector database.
"""
from __future__ import annotations

import math

import pytest

from backend.eval.rag_benchmark import (
    FrozenCase,
    aggregate_benchmark_results,
    score_frozen_case,
)
from backend.eval.rag_manifest import finalize_comparison_signature, sha256_json
from backend.indexer import ChunkWithMeta
from backend.rag_eval_retrievers import EvalRetrievalOutcome
from backend.rag_ids import stable_chunk_id


def _case() -> FrozenCase:
    return FrozenCase(
        case_id="case-001",
        topic="python",
        question="How is the target mechanism implemented?",
        must_include_any=("target",),
        expected_keywords=("alpha", "beta", "gamma"),
        difficulty="hard",
        query_type="semantic_gap",
    )


def _chunk(content: str, score: float = 0.5, *, node_id: str = "") -> ChunkWithMeta:
    return ChunkWithMeta(
        content=content,
        score=score,
        source_file="guide.md",
        header_path="Core > Details",
        node_id=node_id,
    )


def test_stable_chunk_id_distinguishes_sibling_chunks_and_is_reproducible():
    first = stable_chunk_id("first subsection", "guide.md", "Core > Details")
    sibling = stable_chunk_id("second subsection", "guide.md", "Core > Details")

    assert first != sibling
    assert first == stable_chunk_id("first subsection", "guide.md", "Core > Details")
    assert first == stable_chunk_id("  first subsection\r\n", "guide.md", "Core > Details")


def test_score_frozen_case_computes_ranked_metrics_and_keyword_recall():
    outcome = EvalRetrievalOutcome(
        mode="atomic_dense",
        status="ok",
        chunks=[
            _chunk("alpha appears in a distractor", 0.9),
            _chunk("the target mechanism also covers beta", 0.8),
            _chunk("unrelated material", 0.7),
        ],
        latency_ms=12.5,
    )

    row = score_frozen_case(_case(), outcome, k=3, bundle_id="query-001")

    assert row["hit_at_k"] == 1.0
    assert row["rank"] == 2
    assert row["mrr"] == 0.5
    assert row["context_precision"] == pytest.approx(1 / 3)
    assert row["context_recall"] == pytest.approx(2 / 3)
    assert row["ndcg_at_k"] == pytest.approx(1 / math.log2(3))
    assert row["n_chunks"] == 3
    assert row["n_relevant_chunks"] == 1


def test_infrastructure_failure_scores_zero_and_reduces_aggregate_success():
    success = score_frozen_case(
        _case(),
        EvalRetrievalOutcome(
            mode="atomic_dense",
            status="ok",
            chunks=[_chunk("target alpha beta gamma")],
            latency_ms=10.0,
        ),
        k=3,
        bundle_id="query-ok",
    )
    failure = score_frozen_case(
        _case(),
        EvalRetrievalOutcome(
            mode="atomic_dense",
            status="timeout",
            # Even stale candidates attached to a failed outcome must not score.
            chunks=[_chunk("target alpha beta gamma")],
            latency_ms=30.0,
            error_code="timeout",
            error="retrieval timed out",
        ),
        k=3,
        bundle_id="query-timeout",
    )

    for metric in (
        "hit_at_k",
        "mrr",
        "ndcg_at_k",
        "context_precision",
        "context_recall",
    ):
        assert failure[metric] == 0.0
    assert failure["n_chunks"] == 0
    assert failure["outcome"] == "timeout"

    summary = aggregate_benchmark_results([success, failure], [10.0, 30.0])
    assert summary["hit_at_k"] == 0.5
    assert summary["evaluated_questions"] == 1
    assert summary["error_count"] == 1
    assert summary["success_rate"] == 0.5
    assert summary["valid"] is False


def test_aggregate_is_valid_at_exactly_95_percent_success():
    measured = {
        "outcome": "ok",
        "hit_at_k": 1.0,
        "mrr": 1.0,
        "ndcg_at_k": 1.0,
        "context_precision": 1.0,
        "context_recall": 1.0,
    }
    failed = {
        "outcome": "index_not_ready",
        "hit_at_k": 0.0,
        "mrr": 0.0,
        "ndcg_at_k": 0.0,
        "context_precision": 0.0,
        "context_recall": 0.0,
    }

    summary = aggregate_benchmark_results([dict(measured) for _ in range(19)] + [failed], [])

    assert summary["success_rate"] == 0.95
    assert summary["error_count"] == 1
    assert summary["valid"] is True


def test_degraded_measurement_is_valid_but_not_strictly_comparable():
    rows = [{
        "outcome": "degraded",
        "hit_at_k": 1.0,
        "mrr": 1.0,
        "ndcg_at_k": 1.0,
        "context_precision": 1.0,
        "context_recall": 1.0,
    }]

    summary = aggregate_benchmark_results(rows, [12.0])

    assert summary["success_rate"] == 1.0
    assert summary["valid"] is True
    assert summary["degraded_count"] == 1
    assert summary["fully_healthy_rate"] == 0.0
    assert summary["comparable"] is False


def test_execution_profile_is_part_of_comparison_signature():
    base = {
        "comparison_dimensions": {
            "metric_semantics_version": 2,
            "dataset": {"hash": "dataset", "case_ids": ["q1"]},
            "index_revision": "index",
            "k": 8,
        },
    }
    healthy = finalize_comparison_signature(
        {"comparison_dimensions": dict(base["comparison_dimensions"])},
        execution_profile="healthy",
    )
    degraded = finalize_comparison_signature(
        {"comparison_dimensions": dict(base["comparison_dimensions"])},
        execution_profile="degraded",
    )

    assert healthy["comparison_signature"] != degraded["comparison_signature"]


def test_sha256_json_is_stable_across_mapping_key_order():
    left = {
        "dataset": {"version": "2", "id": "rag-suite"},
        "seed": 42,
        "case_ids": ["a", "b"],
    }
    right = {
        "case_ids": ["a", "b"],
        "seed": 42,
        "dataset": {"id": "rag-suite", "version": "2"},
    }

    assert sha256_json(left) == sha256_json(right)
    assert len(sha256_json(left)) == 64


@pytest.mark.parametrize(
    ("status", "measured"),
    [
        ("ok", True),
        ("empty", True),
        ("degraded", True),
        ("timeout", False),
        ("index_not_ready", False),
        ("error", False),
    ],
)
def test_eval_retrieval_outcome_measured_contract(status: str, measured: bool):
    assert EvalRetrievalOutcome(mode="atomic_dense", status=status).measured is measured


def test_eval_retrieval_outcome_candidates_are_ranked_stable_and_cut_off():
    first = _chunk("first candidate", 0.987654321)
    second = _chunk("second candidate", 0.5, node_id="persisted-node-id")
    outcome = EvalRetrievalOutcome(
        mode="atomic_dense",
        status="ok",
        chunks=[first, second],
    )

    candidates = outcome.candidates(cutoff=1)

    assert candidates == [{
        "rank": 1,
        "node_id": stable_chunk_id(first.content, first.source_file, first.header_path),
        "source_file": "guide.md",
        "header_path": "Core > Details",
        "score": 0.987654,
        "preview": "first candidate",
    }]
