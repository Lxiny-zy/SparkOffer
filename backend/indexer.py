"""LlamaIndex indexing for resume and interview knowledge base."""
import asyncio
import json
import logging
import time
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

# Background rebuild lock — prevent concurrent rebuilds for the same (user, topic)
_rebuild_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _cache_get(key: tuple[str, str]) -> "VectorStoreIndex | None":
    """Get index from cache, returning None if expired or missing."""
    entry = _index_cache.get(key)
    if entry is None:
        return None
    expire_time, index = entry
    if time.time() > expire_time:
        _index_cache.pop(key, None)
        return None
    return index


def _cache_set(key: tuple[str, str], index: "VectorStoreIndex"):
    """Set index in cache with TTL. Evicts oldest if over max size."""
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

    .md  → MarkdownNodeParser (preserves Header_1..N metadata so retrieved
           chunks carry their heading path — better signal for embeddings
           and downstream LLM prompts).
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


def build_resume_index(user_id: str, force_rebuild: bool = False) -> VectorStoreIndex:
    """Build or load the resume index."""
    cache_key = (user_id, "resume")
    cached = _cache_get(cache_key)
    if cached is not None and not force_rebuild:
        return cached

    _init_llama_settings()
    resume_path = settings.user_resume_path(user_id)
    cache_dir = settings.user_index_cache_path(user_id) / "resume"

    if cache_dir.exists() and not force_rebuild:
        storage_context = StorageContext.from_defaults(persist_dir=str(cache_dir))
        index = load_index_from_storage(storage_context)
    else:
        docs = SimpleDirectoryReader(
            input_dir=str(resume_path),
            recursive=True,
        ).load_data()
        index = VectorStoreIndex(_build_nodes(docs))
        cache_dir.mkdir(parents=True, exist_ok=True)
        index.storage_context.persist(persist_dir=str(cache_dir))

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

    if cache_dir.exists() and not force_rebuild:
        storage_context = StorageContext.from_defaults(persist_dir=str(cache_dir))
        index = load_index_from_storage(storage_context)
    else:
        if not topic_dir.exists():
            raise FileNotFoundError(f"Knowledge directory not found: {topic_dir}")

        docs = SimpleDirectoryReader(
            input_dir=str(topic_dir),
            recursive=True,
            required_exts=[".md", ".txt", ".py"],
        ).load_data()

        if not docs:
            raise ValueError(f"No documents found in {topic_dir}")

        index = VectorStoreIndex(_build_nodes(docs))
        cache_dir.mkdir(parents=True, exist_ok=True)
        index.storage_context.persist(persist_dir=str(cache_dir))

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
    cache_dir = settings.user_index_cache_path(user_id) / topic
    if cache_dir.exists():
        import shutil
        shutil.rmtree(cache_dir, ignore_errors=True)


def incremental_insert_to_index(topic: str, user_id: str, new_text: str):
    """Insert new content into an existing topic index WITHOUT full rebuild.

    This leverages the existing cached index (memory or disk) and only embeds
    the new content, avoiding the expensive full-directory re-indexing.
    Falls back to invalidation if the index doesn't exist yet.
    """
    cache_key = (user_id, topic)
    try:
        index = build_topic_index(topic, user_id)
        # Create a Document from the new text and insert into existing index
        doc = Document(text=new_text, metadata={"source": "auto_evolution", "topic": topic})
        index.insert(doc)
        # Persist the updated index to disk cache
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
    if cache_key not in _rebuild_locks:
        _rebuild_locks[cache_key] = asyncio.Lock()

    lock = _rebuild_locks[cache_key]
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


def retrieve_topic_context(topic: str, question: str, user_id: str, top_k: int = 5) -> list[str]:
    """Retrieve raw text chunks from topic index (for answer evaluation)."""
    index = build_topic_index(topic, user_id)
    retriever = index.as_retriever(similarity_top_k=top_k)
    nodes = retriever.retrieve(question)
    return [node.get_content() for node in nodes]


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
            await asyncio.to_thread(build_topic_index, key, user_id)
            logger.info("Index warmup ready: topic=%s (%.1fs)", key, time.time() - t0)
        except asyncio.CancelledError:
            logger.info("Index warmup cancelled.")
            raise
        except Exception as e:
            logger.warning(
                "Index warmup failed for topic=%s: %s (lazy-load will retry on demand)",
                key, e,
            )
    logger.info("Index warmup complete: user=%s", user_id)
