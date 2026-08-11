def test_llama_index_qdrant_adapter_imports() -> None:
    """Catch incompatible qdrant-client/LlamaIndex releases before deploy."""
    from llama_index.vector_stores.qdrant import QdrantVectorStore

    assert QdrantVectorStore is not None
