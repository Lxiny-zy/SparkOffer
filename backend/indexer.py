"""LlamaIndex indexing for resume and interview knowledge base."""
import asyncio
import hashlib
import json
import logging
import re
import threading
import time
import weakref
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
from backend.llm_provider import (
    get_llama_llm, get_embedding, get_embedding_for_index,
    _EMBED_BATCH_SIZE, _EMBED_NUM_WORKERS,
)

logger = logging.getLogger("uvicorn")


class IndexNotReady(Exception):
    """请求路径请求加载某 topic 索引，但磁盘/向量库中尚无已建索引，且调用方
    禁止现场重建（build_if_missing=False）。

    抛出它表示「该 topic 的初始向量化还没成功落盘过」——出题等请求路径应捕获后
    降级为空知识库上下文，并把（重）建委派给后台队列（schedule_index_rebuild），
    绝不在 100s 请求预算内同步全量重嵌入。
    """

# In-memory index cache keyed by (user_id, topic_or_resume)
# Entries expire after 1 hour to prevent unbounded memory growth.
_INDEX_CACHE_TTL = 3600.0  # 1 hour
_INDEX_CACHE_MAX_SIZE = 50

_index_cache: dict[tuple[str, str], tuple[float, "VectorStoreIndex"]] = {}  # key -> (expire_time, index)

# _index_cache is read/written from asyncio.to_thread workers AND from
# gather_topic_contexts' ThreadPoolExecutor, so it is genuinely shared across
# threads. Guard every access — an unlocked eviction scan racing a pop() raised
# "dict changed size during iteration" / KeyError mid-retrieval.
_index_cache_lock = threading.Lock()

# Background rebuild lock — prevent concurrent rebuilds for the same (user, topic).
# WeakValueDictionary so idle locks (no in-flight rebuild holding a reference) are
# GC'd instead of accumulating one entry per (user, topic) forever.
_rebuild_locks: "weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock]" = weakref.WeakValueDictionary()

# Thread-level build lock — serializes build_topic_index() per (user, topic) across
# ALL callers (the async rebuild path AND the embedding task-queue path, both of
# which run build_topic_index(force_rebuild=True) in worker threads). Without this
# the two paths could concurrently persist/delete the same index → half-written
# index or a Qdrant collection deleted mid-rebuild. Threading (not asyncio) lock
# because the critical section runs in thread pools, not the event loop.
_build_locks: "weakref.WeakValueDictionary[tuple[str, str], threading.Lock]" = weakref.WeakValueDictionary()
_build_locks_guard = threading.Lock()


def _get_build_lock(cache_key: tuple[str, str]) -> threading.Lock:
    with _build_locks_guard:
        lock = _build_locks.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _build_locks[cache_key] = lock
        return lock


def _cache_get(key: tuple[str, str]) -> "VectorStoreIndex | None":
    """Get index from cache, returning None if expired or missing."""
    with _index_cache_lock:
        entry = _index_cache.get(key)
        if entry is None:
            return None
        expire_time, index = entry
        if time.time() > expire_time:
            _index_cache.pop(key, None)
            return None
        # Refresh TTL on access so a frequently-used index isn't evicted as the
        # "oldest" entry by _cache_set — makes TTL time-since-last-use (true LRU).
        _index_cache[key] = (time.time() + _INDEX_CACHE_TTL, index)
        return index


def _cache_set(key: tuple[str, str], index: "VectorStoreIndex"):
    """Set index in cache with TTL. Evicts oldest if over max size."""
    with _index_cache_lock:
        _index_cache[key] = (time.time() + _INDEX_CACHE_TTL, index)
        if len(_index_cache) > _INDEX_CACHE_MAX_SIZE:
            # Evict expired first
            now = time.time()
            expired = [k for k, (exp, _) in _index_cache.items() if now > exp]
            for k in expired:
                del _index_cache[k]
            # If still over, evict oldest
            if len(_index_cache) > _INDEX_CACHE_MAX_SIZE:
                oldest = min(_index_cache, key=lambda k: _index_cache[k][0])
                del _index_cache[oldest]


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
    if not source_dir.exists():
        return hashes
    for f in sorted(source_dir.rglob("*")):
        if not f.is_file():
            continue
        if required_exts and f.suffix not in required_exts:
            continue
        rel = f.relative_to(source_dir).as_posix()
        hashes[rel] = hashlib.md5(f.read_bytes()).hexdigest()
    return hashes


def _manifest_path(cache_dir: Path) -> Path:
    return cache_dir / "_file_hashes.json"


def _load_manifest(cache_dir: Path) -> dict[str, str]:
    p = _manifest_path(cache_dir)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_manifest(cache_dir: Path, hashes: dict[str, str]):
    cache_dir.mkdir(parents=True, exist_ok=True)
    _manifest_path(cache_dir).write_text(
        json.dumps(hashes, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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


def load_topics(user_id: str) -> dict:
    """Load topics from user's topics.json. Returns {key: {name, icon, dir}}."""
    path = settings.user_topics_path(user_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_topics(topics: dict, user_id: str):
    """Write topics back to user's topics.json."""
    path = settings.user_topics_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(topics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
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
# 健壮性约定（与记忆库后端对齐）：Qdrant 是软依赖。探针失败 / 运行期出错都会降级回
# 本地 SimpleVectorStore（磁盘 persist），绝不让知识库检索因 Qdrant 不可用而崩。

_qdrant_client_singleton = None
_kb_qdrant_healthy: bool | None = None   # None=未探测；探测后缓存健康与否
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
    global _kb_qdrant_healthy
    if not _use_qdrant_kb():
        return False
    if _kb_qdrant_healthy is None:
        try:
            _get_qdrant_client().get_collections()
            _kb_qdrant_healthy = True
            logger.info("Qdrant KB backend healthy.")
        except Exception as e:
            _kb_qdrant_healthy = False
            logger.warning("Qdrant KB 连通性探针失败（qdrant-only，不降级；操作将抛 IndexNotReady）：%s", e)
    return _kb_qdrant_healthy


def _current_embed_dim() -> int:
    """当前 embedding 模型的维度（探测一次后缓存）。用于校验 Qdrant collection 维度。"""
    global _embed_dim_cache
    if _embed_dim_cache is None:
        _embed_dim_cache = len(get_embedding().get_text_embedding("dimension probe"))
    return _embed_dim_cache


def reset_qdrant_state() -> None:
    """清空 Qdrant client / 健康标志 / 维度缓存，使配置变更（换 embedding 通道等）
    对知识库生效 —— 由 llm_provider.invalidate_singletons 调用。"""
    global _qdrant_client_singleton, _kb_qdrant_healthy, _embed_dim_cache
    _qdrant_client_singleton = None
    _kb_qdrant_healthy = None
    _embed_dim_cache = None


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
) -> list:
    """Read a directory file-by-file and chunk each before moving on.

    SimpleDirectoryReader.load_data() materializes EVERY document's full text in
    memory at once; for a topic with many 30万字+ .md files that's a large peak
    before chunking even starts. iter_data() yields one file's documents at a
    time, so we chunk-and-discard each file's raw text and only the (bounded)
    nodes accumulate. Peak memory drops from (all docs + all nodes) to roughly
    (one file's docs + all nodes).
    """
    reader_kwargs = {"input_dir": str(source_dir), "recursive": True}
    if required_exts:
        reader_kwargs["required_exts"] = required_exts
    reader = SimpleDirectoryReader(**reader_kwargs)
    nodes = []
    for file_docs in reader.iter_data():
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
        col_dim = _qdrant_collection_dim(client, collection)
        if col_dim is not None and col_dim != _current_embed_dim():
            if not build_if_missing:
                raise IndexNotReady(f"qdrant collection dim mismatch: {collection}")
            logger.warning(
                "Qdrant KB '%s' 维度=%s 与当前 embedding 维度=%s 不一致，从源文档重建。",
                collection, col_dim, _current_embed_dim(),
            )
            client.delete_collection(collection)
            exists = False
        else:
            return VectorStoreIndex.from_vector_store(vector_store)

    if exists and force_rebuild:
        # Check manifest: skip full rebuild if no source files actually changed
        if manifest_dir:
            old_hashes = _load_manifest(manifest_dir)
            if old_hashes:
                new_hashes = _compute_file_hashes(source_dir, required_exts)
                added, modified, deleted = _diff_file_hashes(old_hashes, new_hashes)
                if not added and not modified and not deleted:
                    logger.info("Qdrant KB %s: no file changes, skip rebuild", collection)
                    return VectorStoreIndex.from_vector_store(vector_store)
        client.delete_collection(collection)  # 重建前清空，避免重复 node

    if not build_if_missing:
        raise IndexNotReady(f"qdrant collection missing: {collection}")

    if not source_dir.exists():
        if allow_empty:
            nodes = []
        else:
            raise FileNotFoundError(f"Knowledge directory not found: {source_dir}")
    else:
        nodes = _load_nodes_streaming(source_dir, required_exts)

    if not nodes and not allow_empty:
        raise ValueError(f"No documents found in {source_dir}")

    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    # Pre-embed with progress (no-op without a callback); the index then reuses the
    # embeddings instead of re-embedding. Dedicated index instance = long timeout +
    # retry + concurrency. Query keeps LlamaSettings.embed_model (fast-fail).
    embed_model = get_embedding_for_index()
    _embed_nodes_with_progress(nodes, embed_model, progress_cb)
    index = VectorStoreIndex(
        nodes, storage_context=storage_context, embed_model=embed_model,
    )
    if manifest_dir:
        _save_manifest(manifest_dir, _compute_file_hashes(source_dir, required_exts))
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
    old_hashes = _load_manifest(cache_dir)
    if not old_hashes:
        return None  # no manifest → first time; full rebuild to establish baseline

    new_hashes = _compute_file_hashes(source_dir, required_exts)
    added, modified, deleted = _diff_file_hashes(old_hashes, new_hashes)

    if not added and not modified and not deleted:
        try:
            storage_context = StorageContext.from_defaults(persist_dir=str(cache_dir))
            index = load_index_from_storage(storage_context)
            logger.info("Incremental update: %s — no file changes, skip rebuild", cache_dir.name)
            return index
        except Exception:
            return None

    unchanged = len(new_hashes) - len(added) - len(modified)
    logger.info(
        "Incremental index update: %s — +%d ~%d -%d (skipping %d unchanged)",
        cache_dir.name, len(added), len(modified), len(deleted), unchanged,
    )

    try:
        storage_context = StorageContext.from_defaults(persist_dir=str(cache_dir))
        index = load_index_from_storage(storage_context)
    except Exception as e:
        logger.warning("Cannot load existing index for incremental update: %s", e)
        return None

    # ── Delete nodes belonging to modified / deleted files ──
    files_to_remove = {Path(f).name for f in modified + deleted}
    if files_to_remove:
        try:
            node_ids_to_delete = [
                nid for nid, node in list(index.docstore.docs.items())
                if (node.metadata or {}).get("file_name", "") in files_to_remove
            ]
            if node_ids_to_delete:
                vs_data = index._vector_store._data
                for nid in node_ids_to_delete:
                    vs_data.embedding_dict.pop(nid, None)
                    vs_data.text_id_to_ref_doc_id.pop(nid, None)
                    vs_data.metadata_dict.pop(nid, None)
                    index.docstore.delete_document(nid, raise_error=False)
                logger.info(
                    "Incremental: removed %d nodes from %d files",
                    len(node_ids_to_delete), len(files_to_remove),
                )
        except Exception as e:
            logger.warning("Incremental node deletion failed, falling back to full rebuild: %s", e)
            return None

    # ── Embed and insert nodes for added / modified files ──
    files_to_add = [f for f in added + modified if (source_dir / f).exists()]
    if files_to_add:
        try:
            reader = SimpleDirectoryReader(input_files=[str(source_dir / f) for f in files_to_add])
            new_nodes: list = []
            for file_docs in reader.iter_data():
                new_nodes.extend(_build_nodes(file_docs))
            if new_nodes:
                embed_model = get_embedding_for_index()
                _embed_nodes_with_progress(new_nodes, embed_model, progress_cb)
                index.insert_nodes(new_nodes)
                logger.info(
                    "Incremental: inserted %d nodes from %d files",
                    len(new_nodes), len(files_to_add),
                )
        except Exception as e:
            logger.warning("Incremental node insertion failed, falling back to full rebuild: %s", e)
            return None

    index.storage_context.persist(persist_dir=str(cache_dir))
    _save_manifest(cache_dir, new_hashes)
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
        storage_context = StorageContext.from_defaults(persist_dir=str(cache_dir))
        return load_index_from_storage(storage_context)

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

    if not source_dir.exists():
        if allow_empty:
            nodes = []
        else:
            raise FileNotFoundError(f"Knowledge directory not found: {source_dir}")
    else:
        nodes = _load_nodes_streaming(source_dir, required_exts)

    if not nodes and not allow_empty:
        raise ValueError(f"No documents found in {source_dir}")

    embed_model = get_embedding_for_index()
    _embed_nodes_with_progress(nodes, embed_model, progress_cb)
    index = VectorStoreIndex(nodes, embed_model=embed_model)
    cache_dir.mkdir(parents=True, exist_ok=True)
    index.storage_context.persist(persist_dir=str(cache_dir))
    _save_manifest(cache_dir, _compute_file_hashes(source_dir, required_exts))
    return index


def build_resume_index(user_id: str, force_rebuild: bool = False) -> VectorStoreIndex:
    """Build or load the resume index."""
    cache_key = (user_id, "resume")
    cached = _cache_get(cache_key)
    if cached is not None and not force_rebuild:
        return cached

    _init_llama_settings()
    resume_path = settings.user_resume_path(user_id)
    cache_dir = settings.user_index_cache_path(user_id) / "resume"
    collection = _kb_collection_name(user_id, "resume")

    if _use_qdrant_kb():
        # Qdrant-only：错误收敛成 IndexNotReady，绝不退本地（与 build_topic_index 一致）。
        try:
            index = _build_or_load_qdrant_index(
                collection, resume_path, force_rebuild, allow_empty=True,
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

    _cache_set(cache_key, index)
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
    cache_key = (user_id, topic)
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
        cache_dir = settings.user_index_cache_path(user_id) / topic
        exts = [".md", ".txt", ".py"]

        if _use_qdrant_kb():
            # Qdrant-only：配置走 Qdrant 时绝不退本地。任何 Qdrant 错误（缺 collection /
            # 连接失败）都收敛成 IndexNotReady —— 请求路径据此降级空上下文 + 调度后台重建；
            # 后台重建失败则由任务队列重试。全程不读写本地 .index_cache。
            try:
                index = _build_or_load_qdrant_index(
                    _kb_collection_name(user_id, topic), topic_dir, force_rebuild,
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

        _cache_set(cache_key, index)
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
    if _cache_get((user_id, topic)) is not None:
        return True
    if _use_qdrant_kb():
        try:
            return _get_qdrant_client().collection_exists(_kb_collection_name(user_id, topic))
        except Exception:
            return False
    return (settings.user_index_cache_path(user_id) / topic).exists()


def topic_chunk_count(topic: str, user_id: str) -> int:
    """已索引的 chunk（节点）数量。Qdrant 后端 O(1) 取 collection 点数；本地后端取
    docstore 节点数（不做整索引加载）。取不到 / 未建 返回 0。"""
    if _use_qdrant_kb():
        try:
            client = _get_qdrant_client()
            collection = _kb_collection_name(user_id, topic)
            return client.count(collection).count if client.collection_exists(collection) else 0
        except Exception as e:
            logger.warning("topic_chunk_count (qdrant) failed for %s/%s: %s", topic, user_id, e)
            return 0
    # 本地：优先用已缓存的内存索引；否则数 docstore.json 的节点（比向量文件小，免整载）。
    cached = _cache_get((user_id, topic))
    if cached is not None:
        try:
            return len(cached.docstore.docs)
        except Exception:
            pass
    docstore = settings.user_index_cache_path(user_id) / topic / "docstore.json"
    if docstore.exists():
        try:
            data = json.loads(docstore.read_text(encoding="utf-8"))
            return len(data.get("docstore/data") or {})
        except Exception:
            pass
    return 0


def query_resume(question: str, user_id: str, top_k: int = 3) -> str:
    """Query the resume index. Returns "" if the resume index is unavailable
    (Qdrant-only: a backend failure degrades to no resume context, never crashes
    the job_prep / resume_interview callers)."""
    try:
        index = build_resume_index(user_id)
        engine = index.as_query_engine(similarity_top_k=top_k)
        response = engine.query(question)
        return str(response)
    except Exception as e:
        logger.warning("query_resume degraded to empty (resume index unavailable): %s", e)
        return ""


def query_topic(topic: str, question: str, user_id: str, top_k: int = 5) -> str:
    """Query a topic knowledge base."""
    index = build_topic_index(topic, user_id)
    engine = index.as_query_engine(similarity_top_k=top_k)
    response = engine.query(question)
    return str(response)


def invalidate_topic_index(topic: str, user_id: str):
    """Remove cached index for a topic so it gets rebuilt on next access.

    NOTE: Prefer incremental_insert_to_index() for knowledge evolution scenarios
    to avoid costly full rebuilds. This function should only be used when the
    knowledge base has been fundamentally restructured (files deleted, renamed, etc.).
    """
    cache_key = (user_id, topic)
    with _index_cache_lock:  # see lock comment: every access must hold it
        _index_cache.pop(cache_key, None)
    # 配置了 Qdrant 就尽力删掉对应 collection（即便当前降级到本地，也清掉残留）。
    if _use_qdrant_kb():
        try:
            client = _get_qdrant_client()
            collection = _kb_collection_name(user_id, topic)
            if client.collection_exists(collection):
                client.delete_collection(collection)
        except Exception as e:
            logger.warning("Failed to drop Qdrant kb collection %s/%s: %s", user_id, topic, e)
    # 本地磁盘缓存也一并清掉（默认后端，或 Qdrant 降级期间落地的索引）。
    cache_dir = settings.user_index_cache_path(user_id) / topic
    if cache_dir.exists():
        import shutil
        shutil.rmtree(cache_dir, ignore_errors=True)


def evict_topic_cache(topic: str, user_id: str):
    """Evict only the in-memory cache for a topic, preserving the persisted index.

    Used by file-operation endpoints so the subsequent incremental rebuild can
    load and diff against the existing on-disk index rather than starting from
    scratch.  For a full wipe (explicit "rebuild all"), use
    ``invalidate_topic_index`` instead.
    """
    with _index_cache_lock:
        _index_cache.pop((user_id, topic), None)


def incremental_insert_to_index(topic: str, user_id: str, new_text: str, source: str = "auto_evolution"):
    """Insert new content into an existing topic index WITHOUT full rebuild.

    This leverages the existing cached index (memory or disk) and only embeds
    the new content, avoiding the expensive full-directory re-indexing.
    Falls back to invalidation if the index doesn't exist yet.

    ``source`` tags the node metadata ("auto_evolution" for drill writeback,
    "qa_ingest" for user-confirmed QA-card ingestion) so retrieval/eval can later
    distinguish or down-weight user-deposited knowledge from curated seed docs.
    """
    cache_key = (user_id, topic)
    try:
        index = build_topic_index(topic, user_id)
        # Create a Document from the new text and insert into existing index
        doc = Document(text=new_text, metadata={"source": source, "topic": topic})
        index.insert(doc)  # qdrant-backed 时自动推送到 Qdrant，无需本地 persist
        # 非 Qdrant 后端（本地）才需 insert 后 persist 到磁盘，否则缓存淘汰后新内容丢失。
        if not _use_qdrant_kb():
            cache_dir = settings.user_index_cache_path(user_id) / topic
            cache_dir.mkdir(parents=True, exist_ok=True)
            index.storage_context.persist(persist_dir=str(cache_dir))
        # Update in-memory cache
        _cache_set(cache_key, index)
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
            try:
                from backend.embedding_tasks import get_circuit_breaker
                get_circuit_breaker().record_failure()
            except Exception:
                pass


# ── Safe retrieval timeout (seconds) ──
_RETRIEVAL_TIMEOUT = 60.0

# Upper bound for warming a single topic at startup. A warm disk-cache load is
# seconds; a full rebuild can be longer, but past this we skip to the next topic
# so one slow/unreachable topic can't stall the whole serial warmup queue.
_WARMUP_PER_TOPIC_TIMEOUT = 120.0


from dataclasses import dataclass


@dataclass
class ChunkWithMeta:
    """A retrieved chunk with its similarity score and source metadata."""
    content: str
    score: float
    source_file: str
    header_path: str


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
        results.append(ChunkWithMeta(
            content=node.get_content(),
            score=node.score if hasattr(node, "score") and node.score is not None else 0.0,
            source_file=meta.get("file_name", ""),
            header_path=header_path,
        ))
    return results


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
        return await asyncio.wait_for(
            asyncio.to_thread(
                retrieve_topic_context_with_scores, topic, question, user_id, top_k, build_if_missing,
            ),
            timeout=timeout,
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
) -> list[list[str]]:
    """Run several retrieve_topic_context calls concurrently under one overall
    deadline. Returns results aligned to ``requests`` order (each item is a
    (topic_key, question) pair); slots that error or are still running when
    ``timeout`` elapses come back as [].

    Replaces the old "fresh ThreadPoolExecutor(max_workers=1) per topic" pattern,
    which ran retrievals serially (worst case len(requests)×timeout). Never blocks
    on stragglers — shutdown(wait=False) lets a hung sync retrieval finish in the
    background while the caller proceeds with whatever completed in time.
    """
    import concurrent.futures

    if not requests:
        return []

    results: list[list[str]] = [[] for _ in requests]
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(len(requests), max_workers))
    )
    try:
        future_to_idx = {
            executor.submit(retrieve_topic_context, topic, question, user_id, top_k): i
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
