"""LlamaIndex indexing for resume and interview knowledge base."""
import asyncio
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

from backend.config import settings
from backend.llm_provider import get_llama_llm, get_embedding

logger = logging.getLogger("uvicorn")

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


def _build_nodes(docs: list) -> list:
    """Route documents to the right node parser by extension.

    .md  → MarkdownNodeParser (preserves the section heading path under the
           "header_path" metadata key so retrieved chunks carry their heading
           breadcrumb — better signal for embeddings and downstream LLM prompts).
    other → SentenceSplitter (LlamaIndex default 1024-token chunking).
    """
    md_docs, other_docs = [], []
    for d in docs:
        fname = (d.metadata.get("file_name") or "").lower()
        if fname.endswith(".md"):
            md_docs.append(d)
        else:
            other_docs.append(d)

    nodes = []
    if md_docs:
        nodes.extend(MarkdownNodeParser().get_nodes_from_documents(md_docs))
    if other_docs:
        nodes.extend(SentenceSplitter().get_nodes_from_documents(other_docs))
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
    """知识库本回合是否真的可用 Qdrant：配置开启 + 连通性探针通过（结果缓存）。

    探针失败缓存为 False，后续直接走本地索引，避免每次 build 都对着挂掉的 Qdrant
    干等 10s 超时 —— 与记忆库工厂「构造时探测、失败即降级 numpy」同构。配置变更时由
    reset_qdrant_state() 清空缓存重新探测。
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
            logger.warning("Qdrant KB 探针失败，知识库降级用本地索引：%s", e)
    return _kb_qdrant_healthy


def _mark_kb_unhealthy() -> None:
    """运行期 Qdrant 操作出错时调用：本进程后续知识库走本地索引，直到状态被重置。"""
    global _kb_qdrant_healthy
    _kb_qdrant_healthy = False


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


def _build_or_load_qdrant_index(
    collection: str, source_dir: Path, force_rebuild: bool,
    required_exts: list[str] | None = None, allow_empty: bool = False,
) -> VectorStoreIndex:
    """Qdrant 版构建/加载：已有 collection 直接 from_vector_store 恢复（不重嵌入），
    否则从磁盘文档构建并写入 Qdrant（不再用本地 persist_dir）。

    加载已有 collection 前会校验其维度与当前 embedding 模型一致；不一致（换过模型）
    则丢弃并从磁盘源文档重建 —— 知识库的真相源在磁盘，重建无损。"""
    from llama_index.vector_stores.qdrant import QdrantVectorStore as _LlamaQdrant

    client = _get_qdrant_client()
    vector_store = _LlamaQdrant(client=client, collection_name=collection)
    exists = client.collection_exists(collection)

    if exists and not force_rebuild:
        col_dim = _qdrant_collection_dim(client, collection)
        if col_dim is not None and col_dim != _current_embed_dim():
            logger.warning(
                "Qdrant KB '%s' 维度=%s 与当前 embedding 维度=%s 不一致，从源文档重建。",
                collection, col_dim, _current_embed_dim(),
            )
            client.delete_collection(collection)
            exists = False
        else:
            return VectorStoreIndex.from_vector_store(vector_store)

    if exists and force_rebuild:
        client.delete_collection(collection)  # 重建前清空，避免重复 node

    if not source_dir.exists():
        if allow_empty:
            docs = []
        else:
            raise FileNotFoundError(f"Knowledge directory not found: {source_dir}")
    else:
        reader_kwargs = {"input_dir": str(source_dir), "recursive": True}
        if required_exts:
            reader_kwargs["required_exts"] = required_exts
        docs = SimpleDirectoryReader(**reader_kwargs).load_data()

    if not docs and not allow_empty:
        raise ValueError(f"No documents found in {source_dir}")

    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    return VectorStoreIndex(_build_nodes(docs), storage_context=storage_context)


def _build_or_load_local_index(
    cache_dir: Path, source_dir: Path, force_rebuild: bool,
    required_exts: list[str] | None = None, allow_empty: bool = False,
) -> VectorStoreIndex:
    """本地 SimpleVectorStore 版构建/加载（默认后端，也是 Qdrant 不可用时的降级路径）。

    已有磁盘 persist 缓存直接加载；否则从源文档构建并 persist 到磁盘。"""
    if cache_dir.exists() and not force_rebuild:
        storage_context = StorageContext.from_defaults(persist_dir=str(cache_dir))
        return load_index_from_storage(storage_context)

    if not source_dir.exists():
        if allow_empty:
            docs = []
        else:
            raise FileNotFoundError(f"Knowledge directory not found: {source_dir}")
    else:
        reader_kwargs = {"input_dir": str(source_dir), "recursive": True}
        if required_exts:
            reader_kwargs["required_exts"] = required_exts
        docs = SimpleDirectoryReader(**reader_kwargs).load_data()

    if not docs and not allow_empty:
        raise ValueError(f"No documents found in {source_dir}")

    index = VectorStoreIndex(_build_nodes(docs))
    cache_dir.mkdir(parents=True, exist_ok=True)
    index.storage_context.persist(persist_dir=str(cache_dir))
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

    if _qdrant_kb_available():
        try:
            index = _build_or_load_qdrant_index(
                collection, resume_path, force_rebuild, allow_empty=True,
            )
        except Exception as e:
            logger.warning("Qdrant KB 不可用（resume/%s），降级本地索引：%s", user_id, e)
            _mark_kb_unhealthy()
            index = _build_or_load_local_index(
                cache_dir, resume_path, force_rebuild, allow_empty=True,
            )
    else:
        index = _build_or_load_local_index(
            cache_dir, resume_path, force_rebuild, allow_empty=True,
        )

    _cache_set(cache_key, index)
    return index


def build_topic_index(topic: str, user_id: str, force_rebuild: bool = False) -> VectorStoreIndex:
    """Build or load index for a specific knowledge topic."""
    cache_key = (user_id, topic)
    cached = _cache_get(cache_key)
    if cached is not None and not force_rebuild:
        return cached

    _init_llama_settings()

    topic_map = get_topic_map(user_id)
    if topic not in topic_map:
        raise ValueError(f"Unknown topic: {topic}. Available: {list(topic_map.keys())}")

    dir_name = topic_map[topic]
    topic_dir = settings.user_knowledge_path(user_id) / dir_name
    cache_dir = settings.user_index_cache_path(user_id) / topic
    exts = [".md", ".txt", ".py"]

    if _qdrant_kb_available():
        try:
            index = _build_or_load_qdrant_index(
                _kb_collection_name(user_id, topic), topic_dir, force_rebuild,
                required_exts=exts,
            )
        except Exception as e:
            logger.warning("Qdrant KB 不可用（%s/%s），降级本地索引：%s", topic, user_id, e)
            _mark_kb_unhealthy()
            index = _build_or_load_local_index(
                cache_dir, topic_dir, force_rebuild, required_exts=exts,
            )
    else:
        index = _build_or_load_local_index(
            cache_dir, topic_dir, force_rebuild, required_exts=exts,
        )

    _cache_set(cache_key, index)
    return index


def query_resume(question: str, user_id: str, top_k: int = 3) -> str:
    """Query the resume index."""
    index = build_resume_index(user_id)
    engine = index.as_query_engine(similarity_top_k=top_k)
    response = engine.query(question)
    return str(response)


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
        # 持久化判定按「实际是否 Qdrant 后端」而非「是否配置 Qdrant」：Qdrant 降级到
        # 本地时，insert 后必须 persist 到磁盘，否则缓存淘汰后新内容丢失。
        if not _qdrant_kb_available():
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


def retrieve_topic_context(topic: str, question: str, user_id: str, top_k: int = 5) -> list[str]:
    """Retrieve raw text chunks from topic index (for answer evaluation)."""
    index = build_topic_index(topic, user_id)
    retriever = index.as_retriever(similarity_top_k=top_k)
    nodes = retriever.retrieve(question)
    return [node.get_content() for node in nodes]


def retrieve_topic_context_with_scores(
    topic: str, question: str, user_id: str, top_k: int = 5,
) -> list[ChunkWithMeta]:
    """Retrieve chunks with similarity scores and source metadata preserved."""
    index = build_topic_index(topic, user_id)
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


async def safe_retrieve_topic_context_with_scores(
    topic: str, question: str, user_id: str,
    top_k: int = 5, timeout: float = _RETRIEVAL_TIMEOUT,
) -> list[ChunkWithMeta]:
    """Async-safe wrapper around retrieve_topic_context_with_scores."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(retrieve_topic_context_with_scores, topic, question, user_id, top_k),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("Knowledge retrieval (scored) timed out for topic=%s", topic)
        return []
    except Exception as e:
        logger.warning("Knowledge retrieval (scored) failed for topic=%s: %s", topic, e)
        return []


async def safe_retrieve_topic_context(
    topic: str, question: str, user_id: str,
    top_k: int = 5, timeout: float = _RETRIEVAL_TIMEOUT,
) -> list[str]:
    """Async-safe wrapper around retrieve_topic_context with timeout protection.

    Returns empty list on timeout or error instead of crashing.
    """
    try:
        chunks = await asyncio.wait_for(
            asyncio.to_thread(retrieve_topic_context, topic, question, user_id, top_k),
            timeout=timeout,
        )
        return chunks
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
      - Topics are warmed SEQUENTIALLY, not concurrently, to stay under the
        embedding API concurrency limit (cf. commit 5ce4d7b).
      - build_topic_index is cache-aware, so an already-warm topic returns fast.
    """
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

    logger.info("Index warmup starting: user=%s topics=%s", user_id, list(topics.keys()))
    for key in topics:
        t0 = time.time()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(build_topic_index, key, user_id),
                timeout=_WARMUP_PER_TOPIC_TIMEOUT,
            )
            logger.info("Index warmup ready: topic=%s (%.1fs)", key, time.time() - t0)
        except asyncio.CancelledError:
            logger.info("Index warmup cancelled.")
            raise
        except asyncio.TimeoutError:
            logger.warning(
                "Index warmup timed out for topic=%s after %.0fs — skipping to next "
                "(lazy-load will retry on demand)",
                key, _WARMUP_PER_TOPIC_TIMEOUT,
            )
        except Exception as e:
            logger.warning(
                "Index warmup failed for topic=%s: %s (lazy-load will retry on demand)",
                key, e,
            )
    logger.info("Index warmup complete: user=%s", user_id)
