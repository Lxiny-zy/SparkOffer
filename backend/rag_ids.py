"""Stable identifiers shared by RAG indexing, retrieval, and evaluation."""
from __future__ import annotations

import hashlib


def stable_chunk_id(
    content: str,
    source_file: str = "",
    header_path: str = "",
) -> str:
    """Return a deterministic id for one logical chunk.

    ``file + header`` is not sufficient because an oversized Markdown section is
    split into several chunks that retain the same metadata. Including the
    normalized content hash prevents those siblings from being mistaken for the
    same qrel while keeping the id stable across index rebuilds and vector stores.
    """
    normalized = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    identity = "\0".join((source_file or "", header_path or "", normalized))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
