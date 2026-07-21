"""Reranker cache identity must follow the active provider."""
from backend.reranker import _cache_key, _validated_indices


def test_reranker_cache_key_changes_with_model_and_endpoint():
    base = _cache_key(
        "question", ["first", "second"], 2,
        model="rerank-v1", endpoint="https://provider-a.example/v1",
    )

    assert base == _cache_key(
        "question", ["first", "second"], 2,
        model="rerank-v1", endpoint="https://provider-a.example/v1/",
    )
    assert base != _cache_key(
        "question", ["first", "second"], 2,
        model="rerank-v2", endpoint="https://provider-a.example/v1",
    )
    assert base != _cache_key(
        "question", ["first", "second"], 2,
        model="rerank-v1", endpoint="https://provider-b.example/v1",
    )


def test_validated_indices_accepts_safe_order():
    assert _validated_indices([2, 0], chunk_count=3, max_results=2) == [2, 0]


def test_validated_indices_rejects_malformed_values():
    assert _validated_indices([-1], chunk_count=3, max_results=1) is None
    assert _validated_indices([0, 0], chunk_count=3, max_results=2) is None
    assert _validated_indices([3], chunk_count=3, max_results=1) is None
    assert _validated_indices([True], chunk_count=3, max_results=1) is None
    assert _validated_indices([0, 1, 2], chunk_count=3, max_results=2) is None
