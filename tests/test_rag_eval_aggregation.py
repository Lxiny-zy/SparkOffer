"""Aggregation semantics for synthetic end-to-end RAG evaluation."""
from __future__ import annotations

from backend.rag_eval import QuestionResult, _aggregate, _support_weight


def _result(**overrides) -> QuestionResult:
    values = {
        "question": "question",
        "reference_answer": "reference",
        "generated_answer": "answer",
        "rank": 1,
        "hit": 1,
        "trivial_hit": False,
        "loo_hit": 1,
        "context_precision": 1.0,
        "context_recall": 1.0,
        "faithfulness": 1.0,
        "answer_relevancy": 1.0,
        "answer_correctness": 1.0,
        "match_method": "identity",
        "gold_source": "guide.md",
        "retrieval_status": "ok",
        "generation_success": True,
        "judge_successes": 4,
        "judge_attempts": 4,
        "metric_observation_success": True,
    }
    values.update(overrides)
    return QuestionResult(**values)


def test_healthy_synthetic_observations_are_comparable():
    summary = _aggregate([_result()], error_count=0, total_questions=1)

    assert summary.valid is True
    assert summary.generation_success_rate == 1.0
    assert summary.judge_observed_rate == 1.0
    assert summary.metric_observation_rate == 1.0
    assert summary.comparable is True


def test_judge_failure_is_zero_quality_but_not_a_comparable_baseline():
    summary = _aggregate(
        [_result(context_recall=None, judge_successes=3)],
        error_count=0,
        total_questions=1,
    )

    assert summary.success_rate == 1.0
    assert summary.valid is True
    assert summary.context_recall is None
    assert summary.judge_observed_rate == 0.75
    assert summary.comparable is False


def test_generation_or_embedding_metric_failure_blocks_comparison():
    summary = _aggregate(
        [_result(
            generated_answer="",
            generation_success=False,
            judge_successes=1,
            metric_observation_success=False,
        )],
        error_count=0,
        total_questions=1,
    )

    assert summary.valid is True
    assert summary.generation_success_rate == 0.0
    assert summary.metric_observation_rate == 0.0
    assert summary.comparable is False


def test_legacy_string_false_is_not_counted_as_supported():
    assert _support_weight({"supported": "false"}) == 0.0
    assert _support_weight({"supported": "0"}) == 0.0
    assert _support_weight({"supported": "true"}) == 1.0
