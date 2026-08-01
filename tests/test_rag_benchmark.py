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
    # nDCG normalizes against a saturated ideal (all k slots relevant), not
    # against however many relevant chunks came back — the latter is non-monotone
    # in relevance. One relevant chunk at rank 2 out of k=3 slots.
    ideal_at_3 = sum(1 / math.log2(rank + 1) for rank in range(1, 4))
    assert row["ndcg_at_k"] == pytest.approx((1 / math.log2(3)) / ideal_at_3)
    assert row["n_chunks"] == 3
    assert row["n_relevant_chunks"] == 1


def test_ndcg_is_monotone_in_relevance():
    """Adding a relevant chunk must never lower the score.

    The previous ideal-DCG (built from the number of relevant chunks actually
    returned) made retrieving *more* correct material score *worse*: rank-1-only
    scored 1.000, rank 1+8 scored 0.807.
    """
    def _row_for(relevant_ranks: set[int], k: int = 8):
        chunks = [
            _chunk("target alpha" if rank in relevant_ranks else "unrelated filler")
            for rank in range(1, k + 1)
        ]
        outcome = EvalRetrievalOutcome(
            mode="atomic_dense", status="ok", chunks=chunks, latency_ms=1.0,
        )
        return score_frozen_case(_case(), outcome, k=k, bundle_id="query-001")

    only_first = _row_for({1})["ndcg_at_k"]
    first_and_last = _row_for({1, 8})["ndcg_at_k"]
    three_relevant = _row_for({1, 7, 8})["ndcg_at_k"]
    everything = _row_for(set(range(1, 9)))["ndcg_at_k"]

    assert only_first < first_and_last < three_relevant < everything
    assert everything == pytest.approx(1.0)


def test_shared_context_suppresses_unattributable_rank_metrics():
    """Bundled cases share one top-k, so rank-sensitive metrics are not measured.

    At bundle_size=B the protocol alone caps MRR/nDCG/precision regardless of
    retriever quality, and rank 1 may belong to a different question entirely.
    Those metrics must be None (unmeasured), not 0.0 (measured as bad).
    """
    outcome = EvalRetrievalOutcome(
        mode="production_replay",
        status="ok",
        chunks=[_chunk("target alpha beta"), _chunk("someone else's chunk")],
        latency_ms=5000.0,
    )

    row = score_frozen_case(
        _case(), outcome, k=8, bundle_id="bundle-001", bundle_size=5,
    )

    assert row["mrr"] is None
    assert row["ndcg_at_k"] is None
    assert row["context_precision"] is None
    # Hit@K and recall only ask whether this case's evidence appeared at all.
    assert row["hit_at_k"] == 1.0
    assert row["context_recall"] == pytest.approx(2 / 3)
    assert row["best_rank"] == 1
    # The attempt served 5 questions; per-question cost is the amortized one.
    assert row["latency_ms_per_question"] == pytest.approx(1000.0)


def test_unmeasured_metrics_leave_the_denominator_instead_of_scoring_zero():
    shared = [
        score_frozen_case(
            _case(),
            EvalRetrievalOutcome(
                mode="production_replay", status="ok",
                chunks=[_chunk("target alpha beta gamma")], latency_ms=100.0,
            ),
            k=8, bundle_id="bundle-001", bundle_size=5,
        )
        for _ in range(5)
    ]

    summary = aggregate_benchmark_results(shared, [100.0])

    assert summary["hit_at_k"] == 1.0
    assert summary["mrr"] is None
    assert summary["ndcg_at_k"] is None
    assert summary["context_precision"] is None
    assert summary["latency_unit"] == "bundle"
    assert summary["n_latency_samples"] == 1
    # One sample cannot support a tail percentile.
    assert summary["latency_p95_ms"] is None


def test_own_subquery_failure_is_attributed_to_its_case():
    """A case whose own query failed was not observed — it must not score zero.

    The surviving queries' chunks are an observation about *those* questions, so
    scoring this case against them turns an infrastructure failure into a
    quality result.
    """
    outcome = EvalRetrievalOutcome(
        mode="production_replay",
        status="degraded",
        chunks=[_chunk("target alpha beta gamma")],
        latency_ms=4000.0,
        error_code="partial_retrieval_failure",
        trace={"failure_details": [{"query_index": 1, "code": "index_not_ready"}]},
    )

    survived = score_frozen_case(
        _case(), outcome, k=8, bundle_id="bundle-001", bundle_size=2,
        own_query_failed=False,
    )
    failed = score_frozen_case(
        _case(), outcome, k=8, bundle_id="bundle-001", bundle_size=2,
        own_query_failed=True,
    )

    assert survived["hit_at_k"] == 1.0
    assert failed["hit_at_k"] == 0.0
    assert failed["n_chunks"] == 0
    assert failed["outcome"] == "query_failed"
    assert failed["error_code"] == "own_subquery_failed"

    summary = aggregate_benchmark_results([survived, failed], [4000.0])
    assert summary["evaluated_questions"] == 1
    assert summary["error_count"] == 1


def test_hit_at_k_reports_a_confidence_interval_for_the_small_frozen_set():
    rows = [
        {"outcome": "ok", "hit_at_k": 1.0, "context_recall": 1.0}
        for _ in range(29)
    ]

    summary = aggregate_benchmark_results(rows, [100.0] * 29)
    ci = summary["hit_at_k_ci95"]

    assert summary["hit_at_k"] == 1.0
    assert ci["n"] == 29
    assert ci["point"] == 1.0
    # 29/29 does not license claiming a true rate of 1.00.
    assert ci["high"] == 1.0
    assert 0.85 < ci["low"] < 0.90


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
