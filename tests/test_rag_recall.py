"""Unit tests for the RAG-recall scoring helpers (backend/eval/rag_recall.py).

These functions ARE the retrieval-quality ruler. If they're wrong, every
HitRate / MRR / KwCoverage / Precision number downstream is wrong — so they
need their own regression guard before we trust any baseline.
"""
import pytest

from backend.eval.rag_recall import _chunk_hits, _score_query


# ── _chunk_hits ───────────────────────────────────────────────────────────────

def test_chunk_hits_case_insensitive():
    assert _chunk_hits("The GIL is released periodically", ["gil"]) is True


def test_chunk_hits_cjk_substring():
    assert _chunk_hits("多头注意力机制并行计算", ["多头"]) is True


def test_chunk_hits_any_of_multiple_keywords():
    assert _chunk_hits("only mentions attention here", ["multi-head", "attention"]) is True


def test_chunk_hits_miss():
    assert _chunk_hits("completely unrelated text", ["gil", "mvcc"]) is False


def test_chunk_hits_ignores_empty_keyword():
    assert _chunk_hits("some text", [""]) is False


# ── _score_query ──────────────────────────────────────────────────────────────

def test_hit_rate_and_mrr_first_position():
    q = {"must_include_any": ["gil"], "expected_keywords": ["gil"]}
    m = _score_query(["talks about gil", "unrelated"], q, k=5)
    assert m["hit_rate"] == 1.0
    assert m["mrr"] == 1.0  # first chunk hits → 1/1


def test_mrr_second_position():
    q = {"must_include_any": ["gil"]}
    m = _score_query(["unrelated", "mentions gil"], q, k=5)
    assert m["mrr"] == 0.5  # first hit at rank 2 → 1/2


def test_full_miss_zeroes_all():
    q = {"must_include_any": ["gil"], "expected_keywords": ["gil"]}
    m = _score_query(["foo", "bar"], q, k=5)
    assert m["hit_rate"] == 0.0
    assert m["mrr"] == 0.0
    assert m["kw_cov"] == 0.0


def test_keyword_coverage_fraction():
    q = {"must_include_any": ["x"], "expected_keywords": ["alpha", "beta", "gamma"]}
    m = _score_query(["has alpha and beta", "nothing"], q, k=5)
    assert m["kw_cov"] == pytest.approx(2 / 3)  # alpha+beta covered, gamma not


def test_precision_is_hits_over_returned():
    q = {"must_include_any": ["gil"]}
    m = _score_query(["gil here", "gil there", "nope"], q, k=5)
    assert m["precision"] == pytest.approx(2 / 3)  # 2 hit chunks of 3 returned


def test_empty_chunks_is_safe():
    q = {"must_include_any": ["gil"], "expected_keywords": ["gil"]}
    m = _score_query([], q, k=5)
    assert m["hit_rate"] == 0.0
    assert m["precision"] == 0.0
    assert m["n_chunks"] == 0
