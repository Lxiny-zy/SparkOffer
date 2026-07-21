"""Reproducibility metadata for RAG benchmark runs."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import settings


METRIC_SEMANTICS_VERSION = 2


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_topic_corpus(topic: str, user_id: str) -> tuple[str, int]:
    """Hash path + bytes for every indexable file in a user's topic corpus."""
    from backend.indexer import get_topic_map

    topic_map = get_topic_map(user_id)
    if topic not in topic_map:
        raise ValueError(f"Unknown topic: {topic}")
    root = settings.user_knowledge_path(user_id) / topic_map[topic]
    digest = hashlib.sha256()
    count = 0
    if root.exists():
        for path in sorted(
            p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in {".md", ".txt", ".py"}
        ):
            rel = path.relative_to(root).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(hash_file(path).encode("ascii"))
            digest.update(b"\n")
            count += 1
    return digest.hexdigest(), count


def _index_source_snapshot(topic: str, user_id: str) -> dict[str, Any]:
    """Describe the source-file manifest used by the persisted index.

    The live corpus hash and this snapshot are deliberately separate. If source
    files changed but the asynchronous rebuild has not completed yet, the two
    revisions must not be presented as one reproducible index state.
    """
    path = settings.user_index_cache_path(user_id) / topic / "_file_hashes.json"
    if not path.exists():
        return {"status": "missing", "hash": "", "file_count": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("index source manifest is not an object")
        normalized = {str(key): str(value) for key, value in payload.items()}
        return {
            "status": "ok",
            "hash": sha256_json(normalized),
            "file_count": len(normalized),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"status": "corrupt", "hash": "", "file_count": 0}


def _endpoint_hash(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""


def _provider_snapshot() -> dict[str, list[dict[str, Any]]]:
    """Capture provider routing without persisting credentials or raw URLs."""
    from backend.ai_config import get_effective
    from backend.channel_manager import get_all_channels

    result: dict[str, list[dict[str, Any]]] = {}
    for section in ("embedding", "reranker", "llm"):
        channels = get_all_channels(section)
        descriptors: list[dict[str, Any]] = []
        for order, channel in enumerate(channels):
            if not channel.get("enabled", True):
                continue
            model = (
                channel.get("api_model")
                or channel.get("model")
                or channel.get("local_model")
                or ""
            )
            descriptors.append({
                "order": order,
                "id": str(channel.get("id") or ""),
                "priority": int(channel.get("priority", 1)),
                "tier": str(channel.get("tier") or ""),
                "backend": str(channel.get("backend") or "api"),
                "model": str(model),
                "endpoint_hash": _endpoint_hash(channel.get("api_base")),
                "proxy_enabled": bool(channel.get("proxy")),
                "configured": bool(
                    channel.get("api_base")
                    and (channel.get("keys") or channel.get("api_key"))
                ),
            })

        if not descriptors:
            if section == "embedding":
                backend = str(get_effective(section, "backend") or settings.embedding_backend_mode())
                model = (
                    get_effective(section, "api_model")
                    if backend == "api"
                    else get_effective(section, "local_path")
                    or get_effective(section, "local_model")
                    or settings.active_embedding_target()
                )
            elif section == "reranker":
                backend = "api"
                model = get_effective(section, "api_model")
            else:
                backend = "api"
                model = get_effective(section, "model")
            api_base = get_effective(section, "api_base")
            descriptors.append({
                "order": 0,
                "id": "effective-single",
                "priority": 1,
                "tier": "",
                "backend": str(backend),
                "model": str(model or ""),
                "endpoint_hash": _endpoint_hash(api_base),
                "proxy_enabled": False,
                "configured": bool(
                    (backend != "api" and model)
                    or (api_base and get_effective(section, "api_key"))
                ),
            })
        result[section] = descriptors
    return result


def finalize_comparison_signature(
    manifest: dict[str, Any],
    *,
    execution_profile: str | None = None,
) -> dict[str, Any]:
    """Update the deterministic signature after optional runtime observations."""
    dimensions = dict(manifest.get("comparison_dimensions") or {})
    if execution_profile:
        dimensions["execution_profile"] = execution_profile
    manifest["comparison_dimensions"] = dimensions
    manifest["comparison_signature"] = sha256_json(dimensions)
    return manifest


def _git_sha() -> str:
    injected = os.getenv("APP_GIT_SHA", "").strip()
    if injected and injected.lower() != "unknown":
        return injected
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=settings.base_dir,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        ).strip()
    except Exception:
        return injected or "unknown"


def _package_versions() -> dict[str, str]:
    packages = (
        "llama-index-core",
        "llama-index-vector-stores-qdrant",
        "qdrant-client",
        "langchain-core",
        "numpy",
    )
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def build_run_manifest(
    *,
    eval_kind: str,
    retrieval_mode: str,
    topic: str,
    user_id: str,
    dataset_id: str,
    dataset_version: str,
    dataset_hash: str,
    seed: int,
    case_ids: list[str],
    corpus_hash: str,
    corpus_file_count: int,
    k: int,
    judge_mode: str = "none",
    prompt_hash: str = "",
    protocol: dict[str, Any] | None = None,
    retrieval_config_snapshot: dict[str, Any] | None = None,
) -> dict:
    from backend.ai_config import get_config_version, get_retrieval_setting
    from backend.indexer import (
        _CHUNK_OVERLAP,
        _MAX_CHUNK_TOKENS,
        _RETRIEVAL_TIMEOUT,
        topic_chunk_count,
    )

    try:
        indexed_chunk_count = topic_chunk_count(topic, user_id)
    except Exception:
        indexed_chunk_count = None

    retrieval_keys = (
        "per_query_top_k",
        "final_top_n",
        "embed_concurrency",
        "dedup_threshold",
        "end_to_end_timeout",
        "per_query_timeout",
        "reranker_read_timeout",
    )
    retrieval_config = (
        dict(retrieval_config_snapshot)
        if retrieval_config_snapshot is not None
        else {key: get_retrieval_setting(key) for key in retrieval_keys}
    )
    provider_config = _provider_snapshot()
    index_source = _index_source_snapshot(topic, user_id)
    index_payload = {
        "source_manifest": index_source,
        "embedding": provider_config["embedding"],
        "chunk_tokens": _MAX_CHUNK_TOKENS,
        "chunk_overlap": _CHUNK_OVERLAP,
        "vector_backend": settings.vector_backend_mode(),
        "indexed_chunk_count": indexed_chunk_count,
    }
    package_versions = _package_versions()
    runtime = {
        "vector_backend": settings.vector_backend_mode(),
        "indexed_chunk_count": indexed_chunk_count,
        "embedding_backend": settings.embedding_backend_mode(),
        "embedding_target": settings.active_embedding_target(),
        "reranker_model": settings.reranker_api_model or "off-or-channel-managed",
        "llm_model": settings.model or "channel-managed",
        "config_version": get_config_version(),
        "providers": provider_config,
        "packages": package_versions,
    }
    indexing = {
        "chunk_tokens": _MAX_CHUNK_TOKENS,
        "chunk_overlap": _CHUNK_OVERLAP,
        "source_manifest": index_source,
    }
    mode_retrieval_config = (
        retrieval_config
        if retrieval_mode == "production_replay"
        else {
            "top_k": k,
            "per_query_timeout": retrieval_config["per_query_timeout"],
            "indexer_default_timeout": _RETRIEVAL_TIMEOUT,
            "build_if_missing": False,
        }
    )
    manifest = {
        "schema_version": 1,
        "metric_semantics_version": METRIC_SEMANTICS_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "eval_kind": eval_kind,
        "retrieval_mode": retrieval_mode,
        "topic": topic,
        "user_id": user_id,
        "dataset": {
            "id": dataset_id,
            "version": dataset_version,
            "hash": dataset_hash,
            "case_ids": list(case_ids),
        },
        "corpus": {"hash": corpus_hash, "file_count": corpus_file_count},
        "index_revision": sha256_json(index_payload),
        "k": k,
        "judge_mode": judge_mode,
        "seed": seed,
        "git_sha": _git_sha(),
        "runtime": runtime,
        "indexing": indexing,
        "retrieval_config": retrieval_config,
        "prompt_hash": prompt_hash,
        "protocol": protocol or {},
    }
    comparison_providers = {
        "embedding": provider_config["embedding"],
        "reranker": provider_config["reranker"],
    }
    if eval_kind == "synthetic_e2e":
        comparison_providers["llm"] = provider_config["llm"]
    manifest["comparison_dimensions"] = {
        "metric_semantics_version": METRIC_SEMANTICS_VERSION,
        "eval_kind": eval_kind,
        "retrieval_mode": retrieval_mode,
        "topic": topic,
        "dataset": manifest["dataset"],
        "corpus_hash": corpus_hash,
        "index_revision": manifest["index_revision"],
        "k": k,
        "judge_mode": judge_mode if eval_kind == "synthetic_e2e" else "none",
        "retrieval_config": mode_retrieval_config,
        "providers": comparison_providers,
        "vector_backend": runtime["vector_backend"],
        "indexing": indexing,
        "prompt_hash": prompt_hash if eval_kind == "synthetic_e2e" else "",
        "protocol": protocol or {},
        "packages": package_versions,
    }
    return finalize_comparison_signature(manifest)
