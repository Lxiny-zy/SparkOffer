"""Unit tests for Reciprocal Rank Fusion (backend/graphs/rag_retrieval.py).

RRF is the parameter-light merge that combines per-weak-point retrieval
rankings into the final chunk order fed to the LLM. It's a pure function;
a bug here silently reorders which knowledge reaches question generation.

Formula under test:  score(c) = Σ_i  1 / (k + rank_i(c)),  rank starting at 1.
"""
from backend.graphs.rag_retrieval import _reciprocal_rank_fusion, RRF_K


def test_single_ranking_scores_by_rank():
    out = dict(_reciprocal_rank_fusion([["a", "b", "c"]]))
    assert out["a"] == 1.0 / (RRF_K + 1)
    assert out["b"] == 1.0 / (RRF_K + 2)
    assert out["c"] == 1.0 / (RRF_K + 3)


def test_chunk_in_multiple_rankings_accumulates():
    # 'a' is rank 1 in both lists → its score is twice a single rank-1 hit.
    out = dict(_reciprocal_rank_fusion([["a", "b"], ["a", "c"]]))
    assert out["a"] == 2.0 / (RRF_K + 1)


def test_output_sorted_descending_by_fused_score():
    # 'y': rank2 in list1 + rank1 in list2  >  'x': rank1 in list1 only.
    ranked = _reciprocal_rank_fusion([["x", "y", "z"], ["y"]])
    chunks = [c for c, _ in ranked]
    assert chunks[0] == "y"
    assert chunks.index("y") < chunks.index("x")


def test_empty_inputs():
    assert _reciprocal_rank_fusion([]) == []
    assert _reciprocal_rank_fusion([[]]) == []
