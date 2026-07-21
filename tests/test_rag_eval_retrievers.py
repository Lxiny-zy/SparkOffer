"""Failure-semantics tests for the RAG evaluation retrieval adapters."""
from __future__ import annotations

import asyncio

import pytest

import backend.ai_config as ai_config
import backend.indexer as indexer
import backend.rag_eval_retrievers as retrievers
from backend.indexer import ChunkWithMeta, IndexNotReady


def test_atomic_dense_uses_configured_timeout_and_records_it(monkeypatch):
    seen: dict = {}

    async def fake_retrieve(topic, query, user_id, **kwargs):
        seen.update(kwargs)
        return [ChunkWithMeta("answer", 0.9, "guide.md", "Core")]

    monkeypatch.setattr(
        retrievers, "async_retrieve_topic_context_with_scores", fake_retrieve,
    )
    monkeypatch.setattr(
        ai_config,
        "get_retrieval_setting",
        lambda key: 17 if key == "per_query_timeout" else None,
    )

    outcome = asyncio.run(retrievers.retrieve_for_evaluation(
        topic="python",
        user_id="user-1",
        queries=["question"],
        fallback_query="fallback",
        mode="atomic_dense",
        k=4,
    ))

    assert outcome.status == "ok"
    assert seen["timeout"] == 17
    assert seen["build_if_missing"] is False
    assert outcome.trace["retrieval_config"]["per_query_timeout"] == 17


@pytest.mark.parametrize(
    ("failures", "expected_status", "expected_code"),
    [
        (["index_not_ready"] * 3, "index_not_ready", "index_not_ready"),
        (["timeout"] * 3, "timeout", "timeout"),
        (["index_not_ready", "timeout", "runtime"], "error", "mixed_error"),
    ],
)
def test_production_replay_classifies_all_failed_subqueries(
    monkeypatch, failures, expected_status, expected_code,
):
    call_index = 0

    async def fake_retrieve(topic, query, user_id, **kwargs):
        nonlocal call_index
        failure = failures[call_index]
        call_index += 1
        if failure == "index_not_ready":
            raise IndexNotReady("index missing")
        if failure == "timeout":
            raise asyncio.TimeoutError("dense timeout")
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        indexer, "async_retrieve_topic_context_with_scores", fake_retrieve,
    )

    outcome = asyncio.run(retrievers.retrieve_for_evaluation(
        topic="python",
        user_id="user-1",
        queries=["q1", "q2", "q3"],
        fallback_query="fallback",
        mode="production_replay",
        k=4,
    ))

    assert outcome.status == expected_status
    assert outcome.error_code == expected_code
    assert outcome.trace["failed_queries"] == 3
    assert outcome.trace["failure_codes"] == [
        "index_not_ready" if failure == "index_not_ready"
        else "timeout" if failure == "timeout"
        else "RuntimeError"
        for failure in failures
    ]
    assert [item["query_index"] for item in outcome.trace["failure_details"]] == [0, 1, 2]
