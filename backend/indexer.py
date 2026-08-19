"""LlamaIndex indexing for resume and interview knowledge base."""
import asyncio
import hashlib
import json
import logging
import re
import shutil
import tempfile
import threading
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from llama_index.core import (
    SimpleDirectoryReader,
    VectorStoreIndex,
    StorageContext,
    load_index_from_storage,
    Settings as LlamaSettings,
    Document,
)
from llama_index.core.node_parser import MarkdownNodeParser, SentenceSplitter
from llama_index.core.schema import MetadataMode

from backend.config import settings
from backend.ai_config import get_config_version, get_effective
from backend.llm_provider import (
    get_llama_llm, get_embedding, get_embedding_for_index,
    _EMBED_BATCH_SIZE, _EMBED_NUM_WORKERS,
)
from backend.utils.files import atomic_write_text

logger = logging.getLogger("uvicorn")


class IndexNotReady(Exception):
    """请求路径请求加载某 topic 索引，但磁盘/向量库中尚无已建索引，且调用方
    禁止现场重建（build_if_missing=False）。

    抛出它表示「该 topic 的初始向量化还没成功落盘过」——出题等请求路径应捕获后
    降级为空知识库上下文，并把（重）建委派给后台队列（schedule_index_rebuild），
    绝不在 100s 请求预算内同步全量重嵌入。
    """


class IndexSnapshotChanged(RuntimeError):
    """The source files or embedding configuration changed during a build."""

# In-memory index cache keyed by (user_id, topic_or_resume)
# Entries expire after 1 hour to prevent unbounded memory growth.
_INDEX_CACHE_TTL = 3600.0  # 1 hour
_INDEX_CACHE_MAX_SIZE = 50
_RESUME_INDEX_ID = "__resume__"
_TOPIC_INDEX_ESCAPE_PREFIX = "__topic_index__"

_index_cache: dict[
    tuple[str, str], tuple[float, "VectorStoreIndex", str]
] = {}  # key -> (expire_time, index, embedding_fingerprint)

# _index_cache is read/written from asyncio.to_thread workers AND from
# gather_topic_contexts' ThreadPoolExecutor, so it is genuinely shared across
# threads. Guard every access — an unlocked eviction scan racing a pop() raised
# "dict changed size during iteration" / KeyError mid-retrieval.
_index_cache_lock = threading.Lock()

# A queue-full fallback can mark an index dirty while a direct/request-path
# build is still finishing.  The generation closes the race where that older
# build would otherwise publish a fresh fingerprint after the dirty marker was
# removed.  The persisted fingerprint deletion remains the restart-safe marker;
# this process-local generation only orders concurrent writers.
_index_dirty_lock = threading.Lock()
_index_dirty_generation = 0
_index_dirty_generations: dict[Path, int] = {}


def _topic_index_id(topic: str) -> str:
    """Map public topic keys into an index namespace disjoint from resume.

    Most existing topic keys retain their historical storage id.  Keys that
    collide with the internal resume id (or with this escape namespace) are
    encoded injectively, preserving compatibility without exposing resume data.
    """
    folded = topic.casefold()
    if (
        folded == _RESUME_INDEX_ID.casefold()
        or folded.startswith(_TOPIC_INDEX_ESCAPE_PREFIX.casefold())
    ):
        encoded = topic.encode("utf-8").hex()
        return f"{_TOPIC_INDEX_ESCAPE_PREFIX}{encoded}"
    return topic


def _dirty_cache_key(cache_dir: Path) -> Path:
    return cache_dir.resolve(strict=False)


def _get_index_dirty_generation(cache_dir: Path) -> int:
    with _index_dirty_lock:
        return _index_dirty_generations.get(_dirty_cache_key(cache_dir), 0)


def clear_index_cache() -> int:
    """Drop all in-memory indexes without touching persisted stores."""
    with _index_cache_lock:
        count = len(_index_cache)
        _index_cache.clear()
        return count

# Background rebuild lock — prevent concurrent rebuilds for the same (user, topic).
# WeakValueDictionary so idle locks (no in-flight rebuild holding a reference) are
# GC'd instead of accumulating one entry per (user, topic) forever.
_rebuild_locks: "weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock]" = weakref.WeakValueDictionary()

# Thread-level write lock serializes builds, inserts, invalidations, and source
# mutations for one index. It is reentrant because a source mutation may call
# invalidate_topic_index while already holding the same lock.
_build_locks: "weakref.WeakValueDictionary[tuple[str, str], threading.RLock]" = weakref.WeakValueDictionary()
_build_locks_guard = threading.Lock()

# topics.json is a per-user read/modify/write document. Keep the lock process
# local (SQLite remains the source of cross-process coordination) and pair it
# with atomic_write_text so a crash cannot leave truncated JSON.
_topics_locks: dict[str, threading.RLock] = {}
_topics_locks_guard = threading.Lock()


def _get_topics_lock(user_id: str) -> threading.RLock:
    with _topics_locks_guard:
        return _topics_locks.setdefault(user_id, threading.RLock())


@contextmanager
def topics_transaction(user_id: str):
    """Yield a topic mapping and atomically persist it on successful exit."""
    lock = _get_topics_lock(user_id)
    with lock:
        topics = load_topics(user_id)
        yield topics
        save_topics(topics, user_id)


def _get_build_lock(cache_key: tuple[str, str]) -> threading.RLock:
    with _build_locks_guard:
        lock = _build_locks.get(cache_key)
        if lock is None:
            lock = threading.RLock()
            _build_locks[cache_key] = lock
        return lock


@contextmanager
def index_mutation_lock(topic: str, user_id: str):
    """Serialize a topic's source mutation with all index writes."""
    with _get_build_lock((user_id, _topic_index_id(topic))):
        yield


@contextmanager
def resume_index_mutation_lock(user_id: str):
    """Serialize resume source replacement with resume index writes."""
    with _get_build_lock((user_id, _RESUME_INDEX_ID)):
        yield


def _cache_get(key: tuple[str, str]) -> "VectorStoreIndex | None":
    """Get an index only while its embedding identity is still current."""
    with _index_cache_lock:
        entry = _index_cache.get(key)
        if entry is None:
            return None
        expire_time, index, fingerprint = entry
        if time.time() > expire_time or fingerprint != _embedding_fingerprint():
            _index_cache.pop(key, None)
            return None
        # Refresh TTL on access so a frequently-used index isn't evicted as the
        # "oldest" entry by _cache_set — makes TTL time-since-last-use (true LRU).
        _index_cache[key] = (time.time() + _INDEX_CACHE_TTL, index, fingerprint)
        return index


def _cache_set(
    key: tuple[str, str], index: "VectorStoreIndex",
    expected_fingerprint: str | None = None,
) -> bool:
    """Cache an index only if the configuration used to build it is current."""
    with _index_cache_lock:
        current_fingerprint = _embedding_fingerprint()
        fingerprint = expected_fingerprint or current_fingerprint
        if fingerprint != current_fingerprint:
            return False
        _index_cache[key] = (
            time.time() + _INDEX_CACHE_TTL, index, fingerprint,
        )
        if len(_index_cache) > _INDEX_CACHE_MAX_SIZE:
            # Evict expired first
            now = time.time()
            expired = [
                k for k, (exp, _, _) in _index_cache.items() if now > exp
            ]
            for k in expired:
                del _index_cache[k]
            # If still over, evict oldest
            if len(_index_cache) > _INDEX_CACHE_MAX_SIZE:
                oldest = min(_index_cache, key=lambda k: _index_cache[k][0])
                del _index_cache[oldest]
        return True


# ── File hash manifest for incremental index rebuilds ──
# Tracks MD5 hashes of source files so we can diff and only re-embed changed files.


def _compute_file_hashes(
    source_dir: Path, required_exts: list[str] | None = None,
) -> dict[str, str]:
    """Compute MD5 hashes for all knowledge files in a directory.

    Returns ``{relative_posix_path: md5_hex}``.
    Mirrors ``_load_nodes_streaming``'s recursive scan + extension filter.
    """
    hashes: dict[str, str] = {}
    allowed_exts = {e.lower() for e in required_exts} if required_exts else None
    if not source_dir.exists():
        return hashes
    for f in sorted(source_dir.rglob("*")):
        if not f.is_file():
            continue
        if allowed_exts and f.suffix.lower() not in allowed_exts:
            continue
        rel = f.relative_to(source_dir).as_posix()
        hashes[rel] = hashlib.md5(f.read_bytes()).hexdigest()
    return hashes


@contextmanager
def _source_snapshot(
    source_dir: Path, required_exts: list[str] | None = None,
):
    """Copy source files once so parsing and manifest hashes share exact bytes."""
    allowed_exts = {e.lower() for e in required_exts} if required_exts else None
    with tempfile.TemporaryDirectory(prefix="sparkoffer-index-") as tmp:
        snapshot_dir = Path(tmp)
        if source_dir.exists():
            for source in sorted(source_dir.rglob("*")):
                if not source.is_file():
                    continue
                if allowed_exts and source.suffix.lower() not in allowed_exts:
                    continue
                relative = source.relative_to(source_dir)
                target = snapshot_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(source, target)
                except FileNotFoundError as exc:
                    raise IndexSnapshotChanged(
                        f"Source changed while taking index snapshot: {relative}"
                    ) from exc
        yield snapshot_dir, _compute_file_hashes(snapshot_dir, required_exts)


def _manifest_path(cache_dir: Path) -> Path:
    return cache_dir / "_file_hashes.json"


def _embedding_fingerprint() -> str:
    """Stable, secret-free identity for the active embedding configuration."""
    channels: list[dict] = []
    try:
        from backend.channel_manager import get_all_channels

        for channel in get_all_channels("embedding"):
            if not channel.get("enabled", True):
                continue
            backend = str(channel.get("backend") or "api").strip().lower()
            identity = {
                "backend": backend,
                "priority": int(channel.get("priority") or 1),
            }
            if backend == "api":
                identity.update({
                    "api_model": str(channel.get("api_model") or "").strip(),
                    "api_base": str(channel.get("api_base") or "").strip(),
                })
            else:
                identity.update({
                    "local_model": str(channel.get("local_model") or "").strip(),
                    "local_path": str(channel.get("local_path") or "").strip(),
                })
            channels.append(identity)
    except Exception:
        channels = []

    if channels:
        data = {"channels": channels}
    else:
        backend = str(get_effective("embedding", "backend") or "").strip().lower()
        api_base = str(get_effective("embedding", "api_base") or "").strip()
        api_key_present = bool(get_effective("embedding", "api_key"))
        if backend == "api" or (not backend and (api_base or api_key_present)):
            data = {
                "backend": "api",
                "api_model": str(
                    get_effective("embedding", "api_model")
                    or settings.embedding_api_model_name()
                ).strip(),
                "api_base": api_base,
            }
        else:
            local_path = (
                get_effective("embedding", "local_path")
                or settings.local_embedding_model_path()
                or ""
            )
            data = {
                "backend": "local",
                "local_model": str(
                    get_effective("embedding", "local_model")
                    or settings.local_embedding_model_name()
                ).strip(),
                "local_path": str(local_path).strip(),
            }
    raw = json.dumps(data, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _fingerprint_path(cache_dir: Path) -> Path:
    return cache_dir / "_embedding_fingerprint"


def _load_embedding_fingerprint(cache_dir: Path) -> str:
    try:
        return _fingerprint_path(cache_dir).read_text(encoding="ascii").strip()
    except OSError:
        return ""


def _save_embedding_fingerprint(cache_dir: Path, fingerprint: str) -> None:
    atomic_write_text(_fingerprint_path(cache_dir), fingerprint + "\n")


def _discard_embedding_fingerprint(cache_dir: Path) -> None:
    try:
        _fingerprint_path(cache_dir).unlink()
    except FileNotFoundError:
        pass


def _embedding_fingerprint_matches(
    cache_dir: Path, expected_fingerprint: str | None = None,
) -> bool:
    persisted = _load_embedding_fingerprint(cache_dir)
    expected = expected_fingerprint or _embedding_fingerprint()
    return bool(persisted) and persisted == expected


def _load_manifest(cache_dir: Path) -> dict[str, str]:
    p = _manifest_path(cache_dir)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            if not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in data.items()
            ):
                return {}
            return data
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
    return {}


def _save_manifest(cache_dir: Path, hashes: dict[str, str]):
    cache_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        _manifest_path(cache_dir),
        json.dumps(hashes, ensure_ascii=False, indent=2) + "\n",
    )


def _save_manifest_if_unchanged(
    cache_dir: Path,
    source_dir: Path,
    expected_hashes: dict[str, str],
    required_exts: list[str] | None,
    expected_fingerprint: str,
    expected_config_version: int,
    expected_dirty_generation: int | None = None,
) -> bool:
    """Persist only the hashes corresponding to this build's source snapshot."""
    if expected_dirty_generation is None:
        expected_dirty_generation = _get_index_dirty_generation(cache_dir)
    current_hashes = _compute_file_hashes(source_dir, required_exts)
    current_fingerprint = _embedding_fingerprint()
    if (
        current_hashes != expected_hashes
        or current_fingerprint != expected_fingerprint
        or get_config_version() != expected_config_version
        or _get_index_dirty_generation(cache_dir) != expected_dirty_generation
    ):
        logger.info(
            "Source files changed during index build for %s; keeping the old "
            "manifest so the queued follow-up detects the change",
            cache_dir.name,
        )
        _discard_embedding_fingerprint(cache_dir)
        return False
    # Publish metadata while serialized with mark_topic_index_dirty().  A dirty
    # mark either wins before this block (generation mismatch) or wins after it
    # and removes the just-written fingerprint; it can never be overwritten.
    with _index_dirty_lock:
        if (
            _index_dirty_generations.get(_dirty_cache_key(cache_dir), 0)
            != expected_dirty_generation
        ):
            _discard_embedding_fingerprint(cache_dir)
            return False
        _save_manifest(cache_dir, expected_hashes)
        _save_embedding_fingerprint(cache_dir, expected_fingerprint)
    # Close the window between validation and the two atomic metadata writes.
    if (
        _compute_file_hashes(source_dir, required_exts) != expected_hashes
        or _embedding_fingerprint() != expected_fingerprint
        or get_config_version() != expected_config_version
        or _get_index_dirty_generation(cache_dir) != expected_dirty_generation
    ):
        _discard_embedding_fingerprint(cache_dir)
        return False
    return True


def _diff_file_hashes(
    old: dict[str, str], new: dict[str, str],
) -> tuple[list[str], list[str], list[str]]:
    """Compare old and new file hashes.

    Returns ``(added, modified, deleted)`` — each a list of relative paths.
    """
    added = [f for f in new if f not in old]
    deleted = [f for f in old if f not in new]
    modified = [f for f in new if f in old and old[f] != new[f]]
    return added, modified, deleted


# 沉淀文件 ↔ schedule_incremental_insert 的 source 标签映射。
# 这两个文件的内容 = 历次增量插入文本的累积（写回流程先落盘再插入），所以增量
# 重建把整个沉淀文件重新嵌入时，必须同步清掉对应 source 的浮动插入节点——
# 否则同一段沉淀会以「文件 chunk」+「插入节点」两份形式重复参与检索。
_DEPOSIT_FILE_SOURCES = {"自动沉淀.md": "auto_evolution", "用户沉淀_qa.md": "qa_ingest"}


def _diff_with_collateral(
    old_hashes: dict[str, str], new_hashes: dict[str, str],
) -> tuple[list[str], list[str], list[str], set[str], list[str], set[str]]:
    """Diff manifests and derive everything the incremental updaters need.

    Returns ``(added, modified, deleted, files_to_remove, collateral, sources_to_purge)``:
    - ``files_to_remove`` — basenames whose nodes must be deleted (modified + deleted).
    - ``collateral`` — UNCHANGED files that share a basename with a removed one
      (nested dirs): node deletion matches on the flat ``file_name`` metadata, so
      their vectors get wiped too and they must be re-embedded or they silently
      vanish from the index.
    - ``sources_to_purge`` — source tags of deposit files being re-embedded/removed
      (see ``_DEPOSIT_FILE_SOURCES``).
    """
    added, modified, deleted = _diff_file_hashes(old_hashes, new_hashes)
    files_to_remove = {Path(f).name for f in modified + deleted}
    collateral = [
        f for f in new_hashes
        if f in old_hashes and old_hashes[f] == new_hashes[f]
        and Path(f).name in files_to_remove
    ]
    sources_to_purge = {
        _DEPOSIT_FILE_SOURCES[Path(f).name]
        for f in added + modified + deleted
        if Path(f).name in _DEPOSIT_FILE_SOURCES
    }
    return added, modified, deleted, files_to_remove, collateral, sources_to_purge


def load_topics(user_id: str) -> dict:
    """Load topics from user's topics.json. Returns {key: {name, icon, dir}}."""
    path = settings.user_topics_path(user_id)
    with _get_topics_lock(user_id):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_topics(topics: dict, user_id: str):
    """Write topics back to user's topics.json."""
    path = settings.user_topics_path(user_id)
    with _get_topics_lock(user_id):
        atomic_write_text(
            path, json.dumps(topics, ensure_ascii=False, indent=2) + "\n",
        )


def get_topic_map(user_id: str) -> dict[str, str]:
    """Returns {key: dir_name}."""
    return {k: v["dir"] for k, v in load_topics(user_id).items()}


def _init_llama_settings():
    LlamaSettings.llm = get_llama_llm()
    LlamaSettings.embed_model = get_embedding()


# Hard token cap per chunk. MarkdownNodeParser splits ONLY on headings and has no
# size limit, so a long section in a big .md (we've seen 300k+ char files) becomes a
# single huge node. Embedding such a node either 400s (exceeds the model's input
# limit) or times out mid-upload — and crucially the request often never reaches the
# provider, so the failure looks like "no request logged, rebuild failed". Capping
# below any embedding model's input window (8k+) makes every node embeddable. 1024
# matches the SentenceSplitter default already used for non-md docs.
_MAX_CHUNK_TOKENS = 1024
_CHUNK_OVERLAP = 100


def _build_nodes(docs: list) -> list:
    """Route documents to the right node parser by extension, then enforce a
    uniform per-chunk token cap so no single node can blow past the embedding
    model's input limit.

    .md  → MarkdownNodeParser (preserves the section heading path under the
           "header_path" metadata key so retrieved chunks carry their heading
           breadcrumb — better signal for embeddings and downstream LLM prompts),
           THEN SentenceSplitter to break any oversized section. The splitter
           propagates the source node's metadata (header_path survives) and
           leaves already-small sections untouched as single nodes.
    other → SentenceSplitter (LlamaIndex default 1024-token chunking).
    """
    md_docs, other_docs = [], []
    for d in docs:
        fname = (d.metadata.get("file_name") or "").lower()
        if fname.endswith(".md"):
            md_docs.append(d)
        else:
            other_docs.append(d)

    splitter = SentenceSplitter(chunk_size=_MAX_CHUNK_TOKENS, chunk_overlap=_CHUNK_OVERLAP)
    nodes = []
    if md_docs:
        md_nodes = MarkdownNodeParser().get_nodes_from_documents(md_docs)
        # Second pass: re-split only the oversized heading-sections. Small sections
        # pass through as-is, keeping their header_path metadata intact.
        nodes.extend(splitter.get_nodes_from_documents(md_nodes))
    if other_docs:
        nodes.extend(splitter.get_nodes_from_documents(other_docs))
    return nodes


# ── Optional Qdrant backend for the knowledge base ──
# 知识库按 (user_id, topic) 分 collection（LlamaIndex 每个 index 对应一个 store），
# 与记忆库的「单 collection + payload 多租户」互补 —— 两种 multitenancy 策略并存。
#
# 健壮性约定（与记忆库后端对齐）：qdrant-only。配置走 Qdrant 时绝不降级本地——
# 探针失败 / 运行期出错收敛为 IndexNotReady，请求路径降级空上下文并委派后台重建。
# Docker compose 部署默认 VECTOR_BACKEND=qdrant（见 docker-compose.yml）；
# 裸 uvicorn 本地开发默认 numpy/本地 persist。

_qdrant_client_singleton = None
_kb_qdrant_healthy: bool | None = None   # None=未探测；探测后缓存健康与否
_kb_qdrant_checked_at = 0.0
_QDRANT_HEALTH_CACHE_TTL = 10.0
_qdrant_health_lock = threading.Lock()
_embed_dim_cache: int | None = None      # 当前 embedding 维度（探测一次后缓存）


def _use_qdrant_kb() -> bool:
    """配置上是否要求知识库走 Qdrant（VECTOR_BACKEND=qdrant 且配了 QDRANT_URL）。"""
    return bool(settings.qdrant_url) and settings.vector_backend_mode() == "qdrant"


def _get_qdrant_client():
    global _qdrant_client_singleton
    if _qdrant_client_singleton is None:
        from qdrant_client import QdrantClient
        _qdrant_client_singleton = QdrantClient(
            url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=10.0,
        )
    return _qdrant_client_singleton


def _qdrant_kb_available() -> bool:
    """启动期 KB Qdrant 连通性探针 —— 只用于日志/可观测，**不再参与路由**。

    Qdrant-only：路由一律按 `_use_qdrant_kb()`（配置）走 Qdrant，不因探针失败降级本地。
    本函数在启动时被调用一次，给出"Qdrant 是否真的连得上"的运维信号；结果缓存，
    配置变更时由 reset_qdrant_state() 清空。
    """
    global _kb_qdrant_healthy, _kb_qdrant_checked_at
    if not _use_qdrant_kb():
        # Do not retain a successful result from an earlier Qdrant
        # configuration if the backend has since been disabled.
        with _qdrant_health_lock:
            _kb_qdrant_healthy = None
            _kb_qdrant_checked_at = 0.0
        return False
    # Readiness can be hit by several workers/threads at once.  Serialize the
    # short probe and the cache update so a cold start does not fan out a
    # request per health-check caller or race singleton construction.
    with _qdrant_health_lock:
        now = time.monotonic()
        # A transient startup/network failure must not poison readiness forever.
        # Keep a short cache while allowing Docker's periodic probe to recover.
        if (
            _kb_qdrant_healthy is None
            or now - _kb_qdrant_checked_at >= _QDRANT_HEALTH_CACHE_TTL
        ):
            try:
                _get_qdrant_client().get_collections()
                _kb_qdrant_healthy = True
                logger.info("Qdrant KB backend healthy.")
            except Exception as e:
                _kb_qdrant_healthy = False
                logger.warning(
                    "Qdrant KB connectivity probe failed (%s); readiness remains unavailable",
                    type(e).__name__,
                )
            # Timestamp after the call, so the TTL describes the period since
            # the observed result rather than including network latency.
            _kb_qdrant_checked_at = time.monotonic()
        return bool(_kb_qdrant_healthy)


def _current_embed_dim() -> int:
    """当前 embedding 模型的维度（探测一次后缓存）。用于校验 Qdrant collection 维度。"""
    global _embed_dim_cache
    if _embed_dim_cache is None:
        _embed_dim_cache = len(get_embedding().get_text_embedding("dimension probe"))
    return _embed_dim_cache


def reset_qdrant_state() -> None:
    """清空 Qdrant client / 健康标志 / 维度缓存，使配置变更（换 embedding 通道等）
    对知识库生效 —— 由 llm_provider.invalidate_singletons 调用。"""
    global _qdrant_client_singleton, _kb_qdrant_healthy, _kb_qdrant_checked_at, _embed_dim_cache
    with _qdrant_health_lock:
        _qdrant_client_singleton = None
        _kb_qdrant_healthy = None
        _kb_qdrant_checked_at = 0.0
        _embed_dim_cache = None
    clear_index_cache()


def _kb_collection_name(user_id: str, topic: str) -> str:
    """Sanitize 成合法 Qdrant collection 名（字母数字 / 下划线 / 连字符）。"""
    return re.sub(r"[^A-Za-z0-9_-]", "_", f"kb_{user_id}_{topic}")


def _qdrant_collection_dim(client, collection: str) -> int | None:
    """Qdrant collection 的向量维度；取不到返回 None。"""
    try:
        vectors = client.get_collection(collection).config.params.vectors
        return vectors.size if hasattr(vectors, "size") else next(iter(vectors.values())).size
    except Exception:
        return None


def _load_nodes_streaming(
    source_dir: Path, required_exts: list[str] | None = None,
    metadata_source_dir: Path | None = None,
) -> list:
    """Read a directory file-by-file and chunk each before moving on.

    SimpleDirectoryReader.load_data() materializes EVERY document's full text in
    memory at once; for a topic with many 30万字+ .md files that's a large peak
    before chunking even starts. iter_data() yields one file's documents at a
    time, so we chunk-and-discard each file's raw text and only the (bounded)
    nodes accumulate. Peak memory drops from (all docs + all nodes) to roughly
    (one file's docs + all nodes).
    """
    if required_exts:
        allowed = {ext.lower() for ext in required_exts}
        input_files = [
            str(path) for path in sorted(source_dir.rglob("*"))
            if path.is_file() and path.suffix.lower() in allowed
        ]
        if not input_files:
            return []
        # SimpleDirectoryReader's required_exts comparison is case-sensitive;
        # explicit files ensure README.MD and CODE.PY are indexed too.
        reader = SimpleDirectoryReader(input_files=input_files)
    else:
        input_files = [
            str(path) for path in sorted(source_dir.rglob("*")) if path.is_file()
        ]
        if not input_files:
            return []
        reader = SimpleDirectoryReader(input_files=input_files)
    nodes = []
    for file_docs in reader.iter_data():
        if metadata_source_dir is not None:
            _remap_snapshot_metadata(file_docs, source_dir, metadata_source_dir)
        nodes.extend(_build_nodes(file_docs))
    return nodes


def _remap_snapshot_metadata(
    docs: list, snapshot_dir: Path, source_dir: Path,
) -> None:
    """Keep persisted document metadata pointed at the durable source tree."""
    snapshot_root = snapshot_dir.resolve()
    for doc in docs:
        raw_path = (doc.metadata or {}).get("file_path")
        if not raw_path:
            continue
        try:
            relative = Path(raw_path).resolve().relative_to(snapshot_root)
        except (OSError, ValueError):
            continue
        doc.metadata["file_path"] = str(source_dir / relative)


def _load_nodes_for_files(
    source_dir: Path, rel_paths: list[str],
    metadata_source_dir: Path | None = None,
) -> list:
    """Chunk only the given manifest-relative files (incremental re-embed set).

    Paths that vanished between diff and read are skipped — their deletion is
    already reflected in the new manifest, so nothing is lost.
    """
    paths = [source_dir / f for f in rel_paths]
    existing = [str(p) for p in paths if p.exists()]
    if not existing:
        return []
    reader = SimpleDirectoryReader(input_files=existing)
    nodes = []
    for file_docs in reader.iter_data():
        if metadata_source_dir is not None:
            _remap_snapshot_metadata(file_docs, source_dir, metadata_source_dir)
        nodes.extend(_build_nodes(file_docs))
    return nodes


def _embed_nodes_with_progress(nodes, embed_model, progress_cb=None) -> None:
    """Pre-embed nodes in observable windows, reporting (done, total) progress.

    A plain VectorStoreIndex(nodes) embeds internally with no progress signal.
    Instead we embed here in windows and set node.embedding; LlamaIndex's
    embed_nodes() then reuses the pre-set embeddings (it only embeds nodes whose
    .embedding is None), so the subsequent index build does NOT re-embed.

    Uses node.get_content(MetadataMode.EMBED) — the exact text LlamaIndex would
    embed — so vectors are identical to the opaque path. No-op without a callback
    (small/early paths skip the windowing overhead and let the index embed).
    """
    if not progress_cb or not nodes:
        return
    total = len(nodes)
    window = max(1, _EMBED_BATCH_SIZE * max(1, _EMBED_NUM_WORKERS))
    done = 0
    progress_cb(0, total)
    for i in range(0, total, window):
        batch = nodes[i:i + window]
        texts = [n.get_content(metadata_mode=MetadataMode.EMBED) for n in batch]
        embeddings = embed_model.get_text_embedding_batch(texts)
        for n, emb in zip(batch, embeddings):
            n.embedding = emb
        done += len(batch)
        progress_cb(done, total)


def _qdrant_delete_kb_points(client, collection: str,
                             file_names: set[str], sources: set[str]) -> None:
    """Delete points whose flat payload ``file_name`` / ``source`` matches.

    LlamaIndex's QdrantVectorStore flattens node metadata into top-level payload
    keys (verified against a live collection), so filtering on ``file_name`` hits
    exactly the chunks of those files. Deposit-insert nodes carry ``source`` but
    no ``file_name``, so the two filters never overlap.
    """
    from qdrant_client import models as qm

    def _delete_by(key: str, values: set[str]) -> None:
        if not values:
            return
        client.delete(
            collection_name=collection,
            points_selector=qm.FilterSelector(filter=qm.Filter(must=[
                qm.FieldCondition(key=key, match=qm.MatchAny(any=sorted(values))),
            ])),
            wait=True,
        )

    _delete_by("file_name", file_names)
    _delete_by("source", sources)


def _try_incremental_qdrant_update(
    client, collection: str, vector_store, source_dir: Path,
    manifest_dir: Path, required_exts: list[str] | None = None,
    progress_cb=None,
) -> "VectorStoreIndex | None":
    """Incrementally update an existing Qdrant collection from the file manifest.

    Mirrors ``_try_incremental_local_update``: diff the hash manifest, delete the
    changed/removed files' points via payload filter, re-embed only the changed
    files, and refresh the manifest. Returns ``None`` to signal that a full
    rebuild (delete_collection + re-embed everything) is required — no manifest
    yet, embedding model changed, or an unexpected error mid-update.
    """
    build_fingerprint = _embedding_fingerprint()
    build_config_version = get_config_version()
    build_dirty_generation = _get_index_dirty_generation(manifest_dir)
    old_hashes = _load_manifest(manifest_dir)
    if not old_hashes or not _embedding_fingerprint_matches(
        manifest_dir, build_fingerprint,
    ):
        return None  # no baseline (pre-manifest collection) → full rebuild once

    with _source_snapshot(source_dir, required_exts) as (
        snapshot_dir, new_hashes,
    ):
        added, modified, deleted, files_to_remove, collateral, sources_to_purge = (
            _diff_with_collateral(old_hashes, new_hashes)
        )

        # Stale-manifest guard: manifest claims an established baseline but the
        # collection is empty (e.g. recreated Qdrant volume).
        try:
            if new_hashes and client.count(collection).count == 0:
                logger.info(
                    "Qdrant KB %s: collection empty but manifest present, full rebuild",
                    collection,
                )
                return None
        except Exception:
            return None

        if not added and not modified and not deleted:
            if (
                _compute_file_hashes(source_dir, required_exts) != new_hashes
                or _embedding_fingerprint() != build_fingerprint
                or get_config_version() != build_config_version
                or _get_index_dirty_generation(manifest_dir)
                != build_dirty_generation
            ):
                raise IndexSnapshotChanged(
                    f"Source changed during no-op Qdrant rebuild: {collection}"
                )
            logger.info("Qdrant KB %s: no file changes, skip rebuild", collection)
            return VectorStoreIndex.from_vector_store(vector_store)

        # A changed dimension cannot be inserted into the existing collection.
        col_dim = _qdrant_collection_dim(client, collection)
        if col_dim is not None and col_dim != _current_embed_dim():
            logger.info(
                "Qdrant KB %s: dim %s != current %s, full rebuild required",
                collection, col_dim, _current_embed_dim(),
            )
            return None

        files_to_embed = [
            path for path in added + modified + collateral
            if (snapshot_dir / path).exists()
        ]
        logger.info(
            "Qdrant KB %s incremental: +%d ~%d -%d "
            "(re-embedding %d files, keeping %d unchanged)",
            collection, len(added), len(modified), len(deleted),
            len(files_to_embed), len(new_hashes) - len(files_to_embed),
        )

        _discard_embedding_fingerprint(manifest_dir)
        try:
            _qdrant_delete_kb_points(
                client, collection, files_to_remove, sources_to_purge,
            )
            new_nodes = _load_nodes_for_files(
                snapshot_dir, files_to_embed, metadata_source_dir=source_dir,
            )
            embed_model = get_embedding_for_index()
            index = VectorStoreIndex.from_vector_store(
                vector_store, embed_model=embed_model,
            )
            if new_nodes:
                _embed_nodes_with_progress(new_nodes, embed_model, progress_cb)
                index.insert_nodes(new_nodes)
        except Exception as exc:
            logger.warning(
                "Qdrant KB %s incremental update failed, falling back to full rebuild: %s",
                collection, exc,
            )
            return None

        if not _save_manifest_if_unchanged(
            manifest_dir, source_dir, new_hashes, required_exts,
            build_fingerprint, build_config_version,
            build_dirty_generation,
        ):
            raise IndexSnapshotChanged(
                f"Source or embedding config changed during Qdrant rebuild: {collection}"
            )
        return index


def _build_or_load_qdrant_index(
    collection: str, source_dir: Path, force_rebuild: bool,
    required_exts: list[str] | None = None, allow_empty: bool = False,
    progress_cb=None, build_if_missing: bool = True,
    manifest_dir: Path | None = None,
) -> VectorStoreIndex:
    """Qdrant 版构建/加载：已有 collection 直接 from_vector_store 恢复（不重嵌入），
    否则从磁盘文档构建并写入 Qdrant（不再用本地 persist_dir）。

    加载已有 collection 前会校验其维度与当前 embedding 模型一致；不一致（换过模型）
    则丢弃并从磁盘源文档重建 —— 知识库的真相源在磁盘，重建无损。

    ``build_if_missing=False`` 时：collection 不存在（或维度不一致需重建）就抛
    :class:`IndexNotReady`，且**不删除**任何现有 collection——请求路径用它避免现场重建，
    真正的重建交后台队列（force_rebuild=True）处理。"""
    from llama_index.vector_stores.qdrant import QdrantVectorStore as _LlamaQdrant

    client = _get_qdrant_client()
    vector_store = _LlamaQdrant(client=client, collection_name=collection)
    exists = client.collection_exists(collection)

    if exists and not force_rebuild:
        current_fingerprint = _embedding_fingerprint()
        col_dim = _qdrant_collection_dim(client, collection)
        fingerprint_ok = bool(
            manifest_dir and _embedding_fingerprint_matches(
                manifest_dir, current_fingerprint,
            )
        )
        if not fingerprint_ok or (
            col_dim is not None and col_dim != _current_embed_dim()
        ):
            if not build_if_missing:
                raise IndexNotReady(
                    f"qdrant collection embedding identity mismatch: {collection}"
                )
            logger.warning(
                "Qdrant KB '%s' embedding identity changed; rebuilding from source",
                collection,
            )
            if manifest_dir:
                _discard_embedding_fingerprint(manifest_dir)
            client.delete_collection(collection)
            # Re-instantiate: the old instance cached _collection_initialized=True at
            # construction, so its add() would skip create_collection and upload into
            # the void (LlamaIndex QdrantVectorStore probes existence only in __init__).
            vector_store = _LlamaQdrant(client=client, collection_name=collection)
            exists = False
        else:
            return VectorStoreIndex.from_vector_store(vector_store)

    if exists and force_rebuild:
        # Incremental first: diff the file-hash manifest and only re-embed the
        # changed files (the all-or-nothing skip lived here before — any single
        # changed file used to trigger delete_collection + full re-embed).
        if manifest_dir:
            index = _try_incremental_qdrant_update(
                client, collection, vector_store, source_dir,
                manifest_dir, required_exts, progress_cb,
            )
            if index is not None:
                return index
        if manifest_dir:
            _discard_embedding_fingerprint(manifest_dir)
        client.delete_collection(collection)  # 全量重建前清空，避免重复 node
        vector_store = _LlamaQdrant(client=client, collection_name=collection)  # 同上：刷新实例状态

    if not build_if_missing:
        raise IndexNotReady(f"qdrant collection missing: {collection}")

    build_fingerprint = _embedding_fingerprint()
    build_config_version = get_config_version()
    build_dirty_generation = (
        _get_index_dirty_generation(manifest_dir) if manifest_dir else 0
    )
    if not source_dir.exists():
        if not allow_empty:
            raise FileNotFoundError(f"Knowledge directory not found: {source_dir}")
    if manifest_dir:
        _discard_embedding_fingerprint(manifest_dir)

    with _source_snapshot(source_dir, required_exts) as (
        snapshot_dir, expected_hashes,
    ):
        nodes = _load_nodes_streaming(
            snapshot_dir, required_exts, metadata_source_dir=source_dir,
        )
        if not nodes and not allow_empty:
            raise ValueError(f"No documents found in {source_dir}")

        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        embed_model = get_embedding_for_index()
        _embed_nodes_with_progress(nodes, embed_model, progress_cb)
        index = VectorStoreIndex(
            nodes, storage_context=storage_context, embed_model=embed_model,
        )
        if manifest_dir and not _save_manifest_if_unchanged(
            manifest_dir, source_dir, expected_hashes, required_exts,
            build_fingerprint, build_config_version,
            build_dirty_generation,
        ):
            raise IndexSnapshotChanged(
                f"Source or embedding config changed during Qdrant build: {collection}"
            )
        return index


def _try_incremental_local_update(
    cache_dir: Path, source_dir: Path,
    required_exts: list[str] | None = None,
    progress_cb=None,
) -> "VectorStoreIndex | None":
    """Try incremental update of an existing local index.

    Compares a file-hash manifest against the current source directory to
    determine which files were added, modified, or deleted.  Only the changed
    files are re-embedded; unchanged files' vectors are preserved.

    Returns the updated index on success, or ``None`` to signal that a full
    rebuild is needed (no manifest yet, corrupted index, or unexpected error).
    """
    build_fingerprint = _embedding_fingerprint()
    build_config_version = get_config_version()
    build_dirty_generation = _get_index_dirty_generation(cache_dir)
    old_hashes = _load_manifest(cache_dir)
    if not old_hashes or not _embedding_fingerprint_matches(
        cache_dir, build_fingerprint,
    ):
        return None  # no manifest → first time; full rebuild to establish baseline

    with _source_snapshot(source_dir, required_exts) as (
        snapshot_dir, new_hashes,
    ):
        added, modified, deleted, files_to_remove, collateral, sources_to_purge = (
            _diff_with_collateral(old_hashes, new_hashes)
        )

        if not added and not modified and not deleted:
            if (
                _compute_file_hashes(source_dir, required_exts) != new_hashes
                or _embedding_fingerprint() != build_fingerprint
                or get_config_version() != build_config_version
                or _get_index_dirty_generation(cache_dir)
                != build_dirty_generation
            ):
                raise IndexSnapshotChanged(
                    f"Source changed during no-op local rebuild: {cache_dir.name}"
                )
            try:
                storage_context = StorageContext.from_defaults(
                    persist_dir=str(cache_dir),
                )
                index = load_index_from_storage(storage_context)
                logger.info(
                    "Incremental update: %s — no file changes, skip rebuild",
                    cache_dir.name,
                )
                return index
            except Exception:
                return None

        unchanged = len(new_hashes) - len(added) - len(modified) - len(collateral)
        logger.info(
            "Incremental index update: %s — +%d ~%d -%d (skipping %d unchanged)",
            cache_dir.name, len(added), len(modified), len(deleted), unchanged,
        )

        try:
            storage_context = StorageContext.from_defaults(persist_dir=str(cache_dir))
            index = load_index_from_storage(storage_context)
        except Exception as exc:
            logger.warning("Cannot load existing index for incremental update: %s", exc)
            return None

        _discard_embedding_fingerprint(cache_dir)
        if files_to_remove or sources_to_purge:
            try:
                node_ids_to_delete = [
                    nid for nid, node in list(index.docstore.docs.items())
                    if (node.metadata or {}).get("file_name", "") in files_to_remove
                    or (node.metadata or {}).get("source", "") in sources_to_purge
                ]
                if node_ids_to_delete:
                    vs_data = index._vector_store._data
                    for node_id in node_ids_to_delete:
                        vs_data.embedding_dict.pop(node_id, None)
                        vs_data.text_id_to_ref_doc_id.pop(node_id, None)
                        vs_data.metadata_dict.pop(node_id, None)
                        index.docstore.delete_document(node_id, raise_error=False)
                    logger.info(
                        "Incremental: removed %d nodes from %d files",
                        len(node_ids_to_delete), len(files_to_remove),
                    )
            except Exception as exc:
                logger.warning(
                    "Incremental node deletion failed, falling back to full rebuild: %s",
                    exc,
                )
                return None

        files_to_add = [
            path for path in added + modified + collateral
            if (snapshot_dir / path).exists()
        ]
        if files_to_add:
            try:
                new_nodes = _load_nodes_for_files(
                    snapshot_dir, files_to_add, metadata_source_dir=source_dir,
                )
                if new_nodes:
                    embed_model = get_embedding_for_index()
                    _embed_nodes_with_progress(new_nodes, embed_model, progress_cb)
                    index.insert_nodes(new_nodes)
                    logger.info(
                        "Incremental: inserted %d nodes from %d files",
                        len(new_nodes), len(files_to_add),
                    )
            except Exception as exc:
                logger.warning(
                    "Incremental node insertion failed, falling back to full rebuild: %s",
                    exc,
                )
                return None

        index.storage_context.persist(persist_dir=str(cache_dir))
        if not _save_manifest_if_unchanged(
            cache_dir, source_dir, new_hashes, required_exts,
            build_fingerprint, build_config_version,
            build_dirty_generation,
        ):
            raise IndexSnapshotChanged(
                f"Source or embedding config changed during local rebuild: {cache_dir.name}"
            )
        return index


def _build_or_load_local_index(
    cache_dir: Path, source_dir: Path, force_rebuild: bool,
    required_exts: list[str] | None = None, allow_empty: bool = False,
    progress_cb=None, build_if_missing: bool = True,
) -> VectorStoreIndex:
    """本地 SimpleVectorStore 版构建/加载（默认后端，也是 Qdrant 不可用时的降级路径）。

    已有磁盘 persist 缓存直接加载；否则从源文档构建并 persist 到磁盘。

    ``build_if_missing=False`` 时：磁盘无 persist 缓存就抛 :class:`IndexNotReady`
    而非现场全量重嵌入——请求路径用它避免 100s 死线内的同步重建。"""
    if cache_dir.exists() and not force_rebuild:
        if _embedding_fingerprint_matches(cache_dir):
            storage_context = StorageContext.from_defaults(persist_dir=str(cache_dir))
            return load_index_from_storage(storage_context)
        if not build_if_missing:
            raise IndexNotReady(
                f"local index embedding identity mismatch: {cache_dir.name}"
            )
        force_rebuild = True

    # force_rebuild with an existing index on disk → try incremental update
    if force_rebuild and cache_dir.exists():
        result = _try_incremental_local_update(
            cache_dir, source_dir, required_exts, progress_cb,
        )
        if result is not None:
            return result
        # incremental failed → fall through to full rebuild

    if not build_if_missing:
        raise IndexNotReady(f"local index missing: {cache_dir}")

    build_fingerprint = _embedding_fingerprint()
    build_config_version = get_config_version()
    build_dirty_generation = _get_index_dirty_generation(cache_dir)
    if not source_dir.exists():
        if not allow_empty:
            raise FileNotFoundError(f"Knowledge directory not found: {source_dir}")

    _discard_embedding_fingerprint(cache_dir)
    with _source_snapshot(source_dir, required_exts) as (
        snapshot_dir, expected_hashes,
    ):
        nodes = _load_nodes_streaming(
            snapshot_dir, required_exts, metadata_source_dir=source_dir,
        )
        if not nodes and not allow_empty:
            raise ValueError(f"No documents found in {source_dir}")

        embed_model = get_embedding_for_index()
        _embed_nodes_with_progress(nodes, embed_model, progress_cb)
        index = VectorStoreIndex(nodes, embed_model=embed_model)
        cache_dir.mkdir(parents=True, exist_ok=True)
        index.storage_context.persist(persist_dir=str(cache_dir))
        if not _save_manifest_if_unchanged(
            cache_dir, source_dir, expected_hashes, required_exts,
            build_fingerprint, build_config_version,
            build_dirty_generation,
        ):
            raise IndexSnapshotChanged(
                f"Source or embedding config changed during local build: {cache_dir.name}"
            )
        return index


def build_resume_index(user_id: str, force_rebuild: bool = False) -> VectorStoreIndex:
    """Build or load the resume index."""
    cache_key = (user_id, _RESUME_INDEX_ID)
    cached = _cache_get(cache_key)
    if cached is not None and not force_rebuild:
        return cached

    build_lock = _get_build_lock(cache_key)
    with build_lock:
        if not force_rebuild:
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached

        _init_llama_settings()
        resume_path = settings.user_resume_path(user_id)
        cache_dir = settings.user_index_cache_path(user_id) / _RESUME_INDEX_ID
        collection = _kb_collection_name(user_id, _RESUME_INDEX_ID)

        if _use_qdrant_kb():
            # Qdrant-only：错误收敛成 IndexNotReady，绝不退本地（与 build_topic_index 一致）。
            try:
                index = _build_or_load_qdrant_index(
                    collection, resume_path, force_rebuild, allow_empty=True,
                    manifest_dir=cache_dir,
                )
            except IndexNotReady:
                raise
            except Exception as e:
                logger.warning("Qdrant KB 操作失败（resume/%s）：%s", user_id, e)
                raise IndexNotReady(f"qdrant kb unavailable: resume/{user_id}") from e
        else:
            index = _build_or_load_local_index(
                cache_dir, resume_path, force_rebuild, allow_empty=True,
            )

        persisted_fingerprint = _load_embedding_fingerprint(cache_dir)
        if not persisted_fingerprint or not _cache_set(
            cache_key, index, persisted_fingerprint,
        ):
            raise IndexSnapshotChanged(
                f"Embedding configuration changed while loading resume index: {user_id}"
            )
        return index


def build_topic_index(topic: str, user_id: str, force_rebuild: bool = False,
                       progress_cb=None, build_if_missing: bool = True) -> VectorStoreIndex:
    """Build or load index for a specific knowledge topic.

    ``progress_cb(done, total)`` is invoked during a full rebuild's embedding pass
    so callers (the task queue) can surface a real progress bar.

    ``build_if_missing=False`` makes a cold (no in-memory cache, no persisted index)
    lookup raise :class:`IndexNotReady` instead of synchronously re-embedding the
    whole topic — request-path callers pass it to avoid blocking on a rebuild.
    """
    index_id = _topic_index_id(topic)
    cache_key = (user_id, index_id)
    cached = _cache_get(cache_key)
    if cached is not None and not force_rebuild:
        return cached

    # Serialize the actual build per (user, topic) so concurrent rebuild paths
    # don't corrupt the persisted index / Qdrant collection. A local strong ref
    # keeps the WeakValueDictionary entry alive for the duration of the hold.
    build_lock = _get_build_lock(cache_key)
    # Request-path callers (build_if_missing=False) must not block behind a long
    # in-progress rebuild — treat "lock held" as "not ready yet".
    blocking = build_if_missing or force_rebuild
    if not build_lock.acquire(blocking=blocking):
        raise IndexNotReady(f"index rebuild in progress: {topic}")
    try:
        # Double-check: a concurrent builder may have populated the cache while we
        # waited. Only short-circuit for non-forced loads.
        if not force_rebuild:
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached

        _init_llama_settings()

        topic_map = get_topic_map(user_id)
        if topic not in topic_map:
            raise ValueError(f"Unknown topic: {topic}. Available: {list(topic_map.keys())}")

        dir_name = topic_map[topic]
        topic_dir = settings.user_knowledge_path(user_id) / dir_name
        cache_dir = settings.user_index_cache_path(user_id) / index_id
        exts = [".md", ".txt", ".py"]

        if _use_qdrant_kb():
            # Qdrant-only：配置走 Qdrant 时绝不退本地。任何 Qdrant 错误（缺 collection /
            # 连接失败）都收敛成 IndexNotReady —— 请求路径据此降级空上下文 + 调度后台重建；
            # 后台重建失败则由任务队列重试。全程不读写本地 .index_cache。
            try:
                index = _build_or_load_qdrant_index(
                    _kb_collection_name(user_id, index_id), topic_dir, force_rebuild,
                    required_exts=exts, progress_cb=progress_cb, build_if_missing=build_if_missing,
                    manifest_dir=cache_dir,
                )
            except IndexNotReady:
                raise
            except Exception as e:
                logger.warning("Qdrant KB 操作失败（%s/%s）：%s", topic, user_id, e)
                raise IndexNotReady(f"qdrant kb unavailable: {topic}") from e
        else:
            index = _build_or_load_local_index(
                cache_dir, topic_dir, force_rebuild, required_exts=exts,
                progress_cb=progress_cb, build_if_missing=build_if_missing,
            )

        persisted_fingerprint = _load_embedding_fingerprint(cache_dir)
        if not persisted_fingerprint or not _cache_set(
            cache_key, index, persisted_fingerprint,
        ):
            raise IndexSnapshotChanged(
                f"Embedding configuration changed while loading topic index: {topic}"
            )
        return index
    finally:
        build_lock.release()


def topic_index_exists(topic: str, user_id: str) -> bool:
    """True if a built index for (user, topic) can be loaded WITHOUT building it —
    i.e. it's in the in-memory cache or persisted (local dir / Qdrant collection).

    Lets the request path distinguish "retrieval returned nothing because the index
    isn't built yet (rebuild scheduled)" from "index is fine, just no match", so the
    drill timeline can show 「索引重建中」 instead of a silent empty result.
    """
    index_id = _topic_index_id(topic)
    if _cache_get((user_id, index_id)) is not None:
        return True
    if _use_qdrant_kb():
        try:
            cache_dir = settings.user_index_cache_path(user_id) / index_id
            return (
                _get_qdrant_client().collection_exists(
                    _kb_collection_name(user_id, index_id)
                )
                and _embedding_fingerprint_matches(cache_dir)
            )
        except Exception:
            return False
    cache_dir = settings.user_index_cache_path(user_id) / index_id
    return cache_dir.exists() and _embedding_fingerprint_matches(cache_dir)


def topic_chunk_count(topic: str, user_id: str) -> int:
    """已索引的 chunk（节点）数量。Qdrant 后端 O(1) 取 collection 点数；本地后端取
    docstore 节点数（不做整索引加载）。取不到 / 未建 返回 0。"""
    index_id = _topic_index_id(topic)
    if _use_qdrant_kb():
        try:
            client = _get_qdrant_client()
            collection = _kb_collection_name(user_id, index_id)
            cache_dir = settings.user_index_cache_path(user_id) / index_id
            return (
                client.count(collection).count
                if client.collection_exists(collection)
                and _embedding_fingerprint_matches(cache_dir)
                else 0
            )
        except Exception as e:
            logger.warning("topic_chunk_count (qdrant) failed for %s/%s: %s", topic, user_id, e)
            return 0
    # 本地：优先用已缓存的内存索引；否则数 docstore.json 的节点（比向量文件小，免整载）。
    cached = _cache_get((user_id, index_id))
    if cached is not None:
        try:
            return len(cached.docstore.docs)
        except Exception:
            pass
    cache_dir = settings.user_index_cache_path(user_id) / index_id
    if not _embedding_fingerprint_matches(cache_dir):
        return 0
    docstore = cache_dir / "docstore.json"
    if docstore.exists():
        try:
            data = json.loads(docstore.read_text(encoding="utf-8"))
            return len(data.get("docstore/data") or {})
        except Exception:
            pass
    return 0


def retrieve_resume_chunks(question: str, user_id: str, top_k: int = 5) -> list[str]:
    """Retrieve raw resume chunks (no LLM synthesis).

    ``query_resume`` runs the text through a query engine, which costs an extra
    LLM call and can return the literal strings ``"Empty Response"`` / ``"None"``
    — both of which then read as resume *content* downstream. Callers that want
    context for their own prompt should use this retriever instead and treat an
    empty list as "no resume context available". Mirrors
    ``retrieve_topic_context`` for the knowledge base.

    Raises on infrastructure failure so callers can tell "no resume text" apart
    from "retrieval broke".
    """
    index = build_resume_index(user_id)
    retriever = index.as_retriever(similarity_top_k=top_k)
    nodes = retriever.retrieve(question)
    return [content for node in nodes if (content := node.get_content().strip())]


def query_resume(question: str, user_id: str, top_k: int = 3) -> str:
    """Query the resume index. Returns "" if the resume index is unavailable
    (Qdrant-only: a backend failure degrades to no resume context, never crashes
    the job_prep / resume_interview callers)."""
    try:
        index = build_resume_index(user_id)
        engine = index.as_query_engine(similarity_top_k=top_k)
        response = engine.query(question)
        answer = str(response).strip()
        # The synthesizer emits these placeholders when nothing was retrieved;
        # passing them on would put the word "None" into an interview prompt.
        if answer.lower() in {"none", "empty response"}:
            return ""
        return answer
    except Exception as e:
        logger.warning("query_resume degraded to empty (resume index unavailable): %s", e)
        return ""


def query_topic(topic: str, question: str, user_id: str, top_k: int = 5) -> str:
    """Query a topic knowledge base."""
    index = build_topic_index(topic, user_id)
    engine = index.as_query_engine(similarity_top_k=top_k)
    response = engine.query(question)
    return str(response)


def _invalidate_index(index_id: str, user_id: str, *, strict: bool) -> None:
    cache_key = (user_id, index_id)
    cache_dir = settings.user_index_cache_path(user_id) / index_id
    failures: list[tuple[str, Exception]] = []

    # Never delete persisted state underneath an active build. This lock also
    # covers resume builds, which share the same cache-key convention.
    build_lock = _get_build_lock(cache_key)
    with build_lock:
        with _index_cache_lock:  # see lock comment: every access must hold it
            _index_cache.pop(cache_key, None)

        # Remove the validity marker first. Even if a later delete fails, the
        # remaining persisted data cannot be mistaken for a usable index.
        try:
            _discard_embedding_fingerprint(cache_dir)
        except OSError as exc:
            failures.append(("embedding fingerprint", exc))

        if _use_qdrant_kb():
            try:
                client = _get_qdrant_client()
                collection = _kb_collection_name(user_id, index_id)
                if client.collection_exists(collection):
                    client.delete_collection(collection)
            except Exception as exc:
                failures.append(("Qdrant collection", exc))

        if cache_dir.exists():
            try:
                shutil.rmtree(cache_dir)
            except OSError as exc:
                failures.append(("local index cache", exc))

    for target, exc in failures:
        logger.warning(
            "Failed to invalidate %s for %s/%s: %s",
            target, user_id, index_id, exc,
        )
    if strict and failures:
        details = "; ".join(f"{target}: {exc}" for target, exc in failures)
        raise RuntimeError(
            f"Unable to fully invalidate index for {user_id}/{index_id}: {details}"
        ) from failures[0][1]


def invalidate_topic_index(
    topic: str, user_id: str, *, strict: bool = False,
) -> None:
    """Remove a topic index so it gets rebuilt on next access.

    Prefer incremental_insert_to_index() for knowledge evolution. Full
    invalidation is intended for source deletion, renaming, or forced rebuilds.
    """
    _invalidate_index(_topic_index_id(topic), user_id, strict=strict)


def invalidate_resume_index(user_id: str, *, strict: bool = False) -> None:
    """Invalidate the resume index in its reserved internal namespace."""
    _invalidate_index(_RESUME_INDEX_ID, user_id, strict=strict)


def evict_topic_cache(topic: str, user_id: str):
    """Evict only the in-memory cache for a topic, preserving the persisted index.

    Used by file-operation endpoints so the subsequent incremental rebuild can
    load and diff against the existing on-disk index rather than starting from
    scratch.  For a full wipe (explicit "rebuild all"), use
    ``invalidate_topic_index`` instead.
    """
    with _index_cache_lock:
        _index_cache.pop((user_id, _topic_index_id(topic)), None)


def mark_topic_index_dirty(topic: str, user_id: str) -> None:
    """Make a topic's persisted index unusable without deleting its baseline.

    Used when a requested rebuild cannot enter the bounded queue.  Keeping the
    manifest/store allows diagnostics, while removing the fingerprint prevents
    retrieval from re-caching stale vectors.  A generation also rejects an older
    concurrent build that started before this mark.
    """
    global _index_dirty_generation

    index_id = _topic_index_id(topic)
    cache_key = (user_id, index_id)
    cache_dir = settings.user_index_cache_path(user_id) / index_id
    with _index_dirty_lock:
        _index_dirty_generation += 1
        _index_dirty_generations[_dirty_cache_key(cache_dir)] = (
            _index_dirty_generation
        )
        _discard_embedding_fingerprint(cache_dir)
    with _index_cache_lock:
        _index_cache.pop(cache_key, None)


def incremental_insert_to_index(topic: str, user_id: str, new_text: str, source: str = "auto_evolution"):
    """Insert new content into an existing topic index WITHOUT full rebuild.

    This leverages the existing cached index (memory or disk) and only embeds
    the new content, avoiding the expensive full-directory re-indexing.
    Falls back to invalidation if the index doesn't exist yet.

    ``source`` tags the node metadata ("auto_evolution" for drill writeback,
    "qa_ingest" for user-confirmed QA-card ingestion) so retrieval/eval can later
    distinguish or down-weight user-deposited knowledge from curated seed docs.
    """
    index_id = _topic_index_id(topic)
    cache_key = (user_id, index_id)
    cache_dir = settings.user_index_cache_path(user_id) / index_id
    try:
        with _get_build_lock(cache_key):
            build_fingerprint = _embedding_fingerprint()
            build_dirty_generation = _get_index_dirty_generation(cache_dir)
            index = build_topic_index(topic, user_id)
            doc = Document(
                text=new_text, metadata={"source": source, "topic": topic},
            )
            index.insert(doc)
            if (
                _embedding_fingerprint() != build_fingerprint
                or _get_index_dirty_generation(cache_dir)
                != build_dirty_generation
            ):
                raise IndexSnapshotChanged(
                    f"Index state changed during insert: {topic}"
                )
            if not _use_qdrant_kb():
                cache_dir.mkdir(parents=True, exist_ok=True)
                index.storage_context.persist(persist_dir=str(cache_dir))
            if not _cache_set(cache_key, index, build_fingerprint):
                raise IndexSnapshotChanged(
                    f"Embedding configuration changed while caching insert: {topic}"
                )
        logger.info(f"Incremental insert to index: topic={topic}, user={user_id}, text_len={len(new_text)}")
    except Exception as e:
        # If incremental insert fails (e.g. no index exists yet), fall back to invalidation
        # The index will be fully rebuilt on next access
        logger.warning(f"Incremental insert failed for {topic}, falling back to invalidation: {e}")
        invalidate_topic_index(topic, user_id)


async def async_rebuild_topic_index(topic: str, user_id: str):
    """Rebuild topic index in the background (non-blocking).

    Uses a per-(user, topic) lock to prevent concurrent rebuilds.
    No total timeout — a large topic may take hours across many embedding requests.
    Protection against hangs is handled at the individual embedding call level:
      - Each _embed() / _embed_batch() call has its own timeout (see vector_memory.py)
      - Circuit breaker stops calling embedding API if it's consistently failing
      - When circuit breaker is OPEN, embed calls return zero vectors instantly,
        so the rebuild finishes quickly (with degraded quality, but no deadlock)
    """
    cache_key = (user_id, topic)
    # get-or-create with a local strong ref taken before/at the dict insert, so the
    # WeakValueDictionary entry can't be GC'd out from under us. No await in this
    # block → atomic under the single-threaded event loop.
    lock = _rebuild_locks.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _rebuild_locks[cache_key] = lock
    if lock.locked():
        logger.info(f"Index rebuild already in progress for {topic}/{user_id}, skipping.")
        return

    async with lock:
        try:
            await asyncio.to_thread(build_topic_index, topic, user_id, True)
            logger.info(f"Background index rebuild completed: topic={topic}, user={user_id}")
        except Exception as e:
            logger.warning(f"Background index rebuild failed for {topic}/{user_id}: {e}")
            # This legacy entry point does not acquire an embedding-circuit
            # permit.  Failure accounting belongs to the queue worker (which
            # owns the permit) or to vector_memory's admitted embedding calls;
            # reporting here would let an unadmitted/late failure mutate a
            # HALF_OPEN recovery probe.


# ── Safe retrieval timeout (seconds) ──
_RETRIEVAL_TIMEOUT = 60.0

# Upper bound for warming a single topic at startup. A warm disk-cache load is
# seconds; a full rebuild can be longer, but past this we skip to the next topic
# so one slow/unreachable topic can't stall the whole serial warmup queue.
_WARMUP_PER_TOPIC_TIMEOUT = 120.0

@dataclass
class ChunkWithMeta:
    """A retrieved chunk with its similarity score and source metadata."""
    content: str
    score: float
    source_file: str
    header_path: str
    node_id: str = ""


def retrieve_topic_context(topic: str, question: str, user_id: str, top_k: int = 5,
                           build_if_missing: bool = True) -> list[str]:
    """Retrieve raw text chunks from topic index (for answer evaluation)."""
    index = build_topic_index(topic, user_id, build_if_missing=build_if_missing)
    retriever = index.as_retriever(similarity_top_k=top_k)
    nodes = retriever.retrieve(question)
    return [node.get_content() for node in nodes]


def retrieve_topic_context_with_scores(
    topic: str, question: str, user_id: str, top_k: int = 5,
    build_if_missing: bool = True,
) -> list[ChunkWithMeta]:
    """Retrieve chunks with similarity scores and source metadata preserved."""
    index = build_topic_index(topic, user_id, build_if_missing=build_if_missing)
    retriever = index.as_retriever(similarity_top_k=top_k)
    nodes = retriever.retrieve(question)
    results: list[ChunkWithMeta] = []
    for node in nodes:
        meta = node.metadata if hasattr(node, "metadata") else {}
        # MarkdownNodeParser stores the heading path under a single "header_path"
        # key formatted as "/H1/H2/" (it does NOT emit Header_1/Header_2/...).
        # Normalize that slash path into a " > " breadcrumb.
        raw_header = (meta.get("header_path") or "").strip("/")
        header_path = raw_header.replace("/", " > ") if raw_header else ""
        content = node.get_content()
        from backend.rag_ids import stable_chunk_id
        results.append(ChunkWithMeta(
            content=content,
            score=node.score if hasattr(node, "score") and node.score is not None else 0.0,
            source_file=meta.get("file_name", ""),
            header_path=header_path,
            node_id=stable_chunk_id(content, meta.get("file_name", ""), header_path),
        ))
    return results


async def async_retrieve_topic_context_with_scores(
    topic: str, question: str, user_id: str,
    top_k: int = 5, timeout: float = _RETRIEVAL_TIMEOUT,
    build_if_missing: bool = True,
) -> list[ChunkWithMeta]:
    """Strict async wrapper used by evaluators that need truthful failures.

    Unlike ``safe_retrieve_topic_context_with_scores`` this function does not
    turn infrastructure errors into an empty result. Production request paths
    continue using the fail-soft wrapper below.
    """
    return await asyncio.wait_for(
        asyncio.to_thread(
            retrieve_topic_context_with_scores,
            topic,
            question,
            user_id,
            top_k,
            build_if_missing,
        ),
        timeout=timeout,
    )


def _schedule_topic_rebuild(topic: str, user_id: str) -> None:
    """Fire-and-forget background rebuild of a topic index via the embedding task
    queue. Used by the request path when an index is missing (IndexNotReady): the
    drill degrades to empty context now and the index self-heals for next time.

    Submission is deduplicated by the queue's ``rebuild:{user}:{topic}`` task_id, so
    the retrieval fan-out calling this several times for one topic still builds once.
    Lazy import keeps indexer ⇄ embedding_tasks free of an import cycle (mirrors
    async_rebuild_topic_index's lazy import of get_circuit_breaker).
    """
    try:
        from backend.embedding_tasks import schedule_index_rebuild
        schedule_index_rebuild(topic, user_id)
    except Exception as e:
        logger.warning("schedule background rebuild failed for %s/%s: %s", topic, user_id, e)


async def safe_retrieve_topic_context_with_scores(
    topic: str, question: str, user_id: str,
    top_k: int = 5, timeout: float = _RETRIEVAL_TIMEOUT,
    build_if_missing: bool = True,
) -> list[ChunkWithMeta]:
    """Async-safe wrapper around retrieve_topic_context_with_scores."""
    try:
        return await async_retrieve_topic_context_with_scores(
            topic,
            question,
            user_id,
            top_k=top_k,
            timeout=timeout,
            build_if_missing=build_if_missing,
        )
    except IndexNotReady:
        logger.info("Index not built for topic=%s; degrading to empty + scheduling background rebuild", topic)
        _schedule_topic_rebuild(topic, user_id)
        return []
    except asyncio.TimeoutError:
        logger.warning("Knowledge retrieval (scored) timed out for topic=%s", topic)
        return []
    except Exception as e:
        logger.warning("Knowledge retrieval (scored) failed for topic=%s: %s", topic, e)
        return []


async def safe_retrieve_topic_context(
    topic: str, question: str, user_id: str,
    top_k: int = 5, timeout: float = _RETRIEVAL_TIMEOUT,
    build_if_missing: bool = True,
) -> list[str]:
    """Async-safe wrapper around retrieve_topic_context with timeout protection.

    Returns empty list on timeout or error instead of crashing.
    """
    try:
        chunks = await asyncio.wait_for(
            asyncio.to_thread(
                retrieve_topic_context, topic, question, user_id, top_k, build_if_missing,
            ),
            timeout=timeout,
        )
        return chunks
    except IndexNotReady:
        logger.info("Index not built for topic=%s; degrading to empty + scheduling background rebuild", topic)
        _schedule_topic_rebuild(topic, user_id)
        return []
    except asyncio.TimeoutError:
        logger.warning(
            f"Knowledge retrieval timed out ({timeout}s) for topic={topic}, "
            f"question={question[:50]!r}..."
        )
        return []
    except Exception as e:
        logger.warning(f"Knowledge retrieval failed for topic={topic}: {e}")
        return []


def gather_topic_contexts(
    requests: list[tuple[str, str]],
    user_id: str,
    top_k: int = 2,
    timeout: float = _RETRIEVAL_TIMEOUT,
    max_workers: int = 4,
    build_if_missing: bool = False,
) -> list[list[str]]:
    """Run several retrieve_topic_context calls concurrently under one overall
    deadline. Returns results aligned to ``requests`` order (each item is a
    (topic_key, question) pair); slots that error or are still running when
    ``timeout`` elapses come back as [].

    Replaces the old "fresh ThreadPoolExecutor(max_workers=1) per topic" pattern,
    which ran retrievals serially (worst case len(requests)×timeout). Never blocks
    on stragglers — shutdown(wait=False) lets a hung sync retrieval finish in the
    background while the caller proceeds with whatever completed in time.

    ``build_if_missing`` defaults to False: request-path callers must not pay for
    a synchronous full re-embed of a cold index. A missing index degrades to []
    and schedules a background rebuild, matching safe_retrieve_topic_context.
    """
    import concurrent.futures

    if not requests:
        return []

    def _retrieve(topic: str, question: str) -> list[str]:
        try:
            return retrieve_topic_context(
                topic, question, user_id, top_k, build_if_missing,
            )
        except IndexNotReady:
            logger.info(
                "Index not built for topic=%s; degrading to empty + scheduling rebuild",
                topic,
            )
            _schedule_topic_rebuild(topic, user_id)
            return []

    results: list[list[str]] = [[] for _ in requests]
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(len(requests), max_workers))
    )
    try:
        future_to_idx = {
            executor.submit(_retrieve, topic, question): i
            for i, (topic, question) in enumerate(requests)
        }
        done, _ = concurrent.futures.wait(future_to_idx, timeout=timeout)
        for future in done:
            i = future_to_idx[future]
            try:
                results[i] = future.result()
            except Exception as e:
                logger.warning(f"Knowledge retrieval failed for topic={requests[i][0]}: {e}")
        not_done = len(requests) - len(done)
        if not_done:
            logger.warning(
                f"Knowledge retrieval: {not_done}/{len(requests)} topic queries "
                f"exceeded {timeout}s; proceeding with partial results"
            )
    finally:
        executor.shutdown(wait=False)
    return results


# ── Startup warmup ────────────────────────────────────────────────────────────

def _first_user_id() -> str | None:
    """Oldest user's id from the DB — the user whose indices we warm at startup.

    Mirrors scripts/warmup_index.py's lookup so manual and automatic warmup
    target the same user.
    """
    import sqlite3
    db_path = settings.db_path
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT id FROM users ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except Exception as e:
        logger.warning("Index warmup: could not read first user id: %s", e)
        return None


async def warmup_user_indices(user_id: str | None = None) -> None:
    """Pre-load every topic index into the in-process cache at startup.

    Without this, the first retrieval for each topic pays a 12-27s cold start: a
    synchronous load_index_from_storage (disk cache present) or, worse, a full
    rebuild that re-embeds every chunk via the embedding API. Warming on startup
    moves that cost off the user's first request.

    Designed to run fire-and-forget from the app lifespan:
      - Never raises — a failed topic logs and falls back to lazy-load on demand.
      - LOAD-only: warmup never inline-rebuilds. A persisted index loads into the
        in-memory cache; a topic with no built index raises IndexNotReady fast and
        is delegated to the background rebuild queue. So one big/unbuilt topic can
        neither stall (no 100s+ inline re-embed) nor abort the rest of the queue.
      - Topics are warmed SEQUENTIALLY smallest-first, so the cheap common case is
        cached before any time is risked on a large one.
    """
    # Startup operational signal: log the KB index backend + (qdrant) real connectivity
    # once. Does NOT gate routing (qdrant-only keys off _use_qdrant_kb()); purely the
    # boot-time "is Qdrant actually reachable" line mirroring the memory backend log.
    logger.info("KB index backend: %s", "qdrant" if _use_qdrant_kb() else "local")
    if _use_qdrant_kb():
        _qdrant_kb_available()

    if user_id is None:
        user_id = _first_user_id()
    if not user_id:
        logger.info("Index warmup skipped: no user available yet.")
        return

    try:
        topics = load_topics(user_id)
    except Exception as e:
        logger.warning("Index warmup skipped: cannot load topics for %s: %s", user_id, e)
        return
    if not topics:
        logger.info("Index warmup skipped: user %s has no topics.", user_id)
        return

    def _source_weight(key: str) -> int:
        d = settings.user_knowledge_path(user_id) / topics[key].get("dir", key)
        try:
            return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        except OSError:
            return 0
    ordered = sorted(topics, key=_source_weight)

    logger.info("Index warmup starting: user=%s topics=%s", user_id, ordered)
    for key in ordered:
        t0 = time.time()
        try:
            # LOAD only (build_if_missing=False): populate the cache from a persisted
            # index, or raise IndexNotReady instantly for an unbuilt one — no inline
            # re-embed, so no un-cancellable zombie thread to collide with the next
            # topic, which is why this loop no longer has to abort on the first miss.
            await asyncio.wait_for(
                asyncio.to_thread(build_topic_index, key, user_id, build_if_missing=False),
                timeout=_WARMUP_PER_TOPIC_TIMEOUT,
            )
            logger.info("Index warmup ready: topic=%s (%.1fs)", key, time.time() - t0)
        except asyncio.CancelledError:
            logger.info("Index warmup cancelled.")
            raise
        except IndexNotReady:
            logger.info(
                "Index warmup: topic=%s has no built index — scheduling background rebuild",
                key,
            )
            _schedule_topic_rebuild(key, user_id)
        except asyncio.TimeoutError:
            logger.warning(
                "Index warmup: loading topic=%s exceeded %.0fs — skipping; "
                "it will lazy-load on demand", key, _WARMUP_PER_TOPIC_TIMEOUT,
            )
        except Exception as e:
            logger.warning(
                "Index warmup failed for topic=%s: %s (lazy-load will retry on demand)",
                key, e,
            )
    logger.info("Index warmup complete: user=%s", user_id)
