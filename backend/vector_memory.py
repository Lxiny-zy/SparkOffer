"""向量记忆系统（应用层）— embedding + 时间衰减 + 薄弱点语义匹配。

底层向量存取委托给可插拔的 VectorStore 后端（numpy 默认 / qdrant 可选，
见 backend/vector_store/）。本模块只负责：
- 调 embedding（带超时/重试/断路器），算查询与内容向量；
- 时间衰减（14 天半衰期）后处理，使两个后端的最终排序一致；
- 薄弱点去重的列表内 cosine 匹配（profile.json 仍是真相源，向量库当 embedding 缓存）。
"""
import asyncio
import logging
from datetime import datetime

import numpy as np

from backend.llm_provider import get_embedding
from backend.vector_store import MemoryRecord, get_vector_store
# 底层向量工具下沉到了 vector_store.base —— 在此 re-export，保持
# graph.py / rag_retrieval.py 既有的 `from backend.vector_memory import ...` 不变。
from backend.vector_store.base import (
    MAX_VECTORS_PER_USER,
    _cosine_similarity,
    _deserialize,
    _serialize,
)

logger = logging.getLogger("uvicorn")

# Embedding 超时配置（秒）
_EMBED_TIMEOUT_SECONDS = 30.0

SIMILARITY_THRESHOLD = 0.75  # weak point dedup
TIME_DECAY_HALF_LIFE = 14.0  # days
TIME_DECAY_WEIGHT = 0.3      # max 30% score reduction from age
# MAX_VECTORS_PER_USER 从 vector_store.base re-export（见顶部 import）


# ── Embedding helpers ──

_MAX_EMBED_RETRIES = 2  # Retry count for transient failures
_RETRY_BACKOFF_BASE = 1.5  # Exponential backoff base (seconds)


def _zero_vec() -> np.ndarray:
    """embedding 失败/熔断时的零向量兜底，维度跟随当前 embedding 模型。

    历史上这里硬编码 1536；换成 1024 维模型（如 bge-m3）后会写入/返回错误维度，
    污染检索并触发下游混维度 cosine。改为从 indexer 探测当前维度；探测本身失败时
    再退回 1536，至少不抛异常。"""
    try:
        from backend.indexer import _current_embed_dim
        return np.zeros(_current_embed_dim(), dtype=np.float32)
    except Exception:
        return np.zeros(1536, dtype=np.float32)


async def _embed(text: str) -> np.ndarray:
    """Async embed text with timeout, retry, and circuit breaker protection.

    Uses asyncio.to_thread so the blocking llama_index sync call runs in a
    thread pool where asyncio.wait_for can actually cancel it.
    Returns zero vector on failure (graceful degradation).
    """
    from backend.embedding_tasks import get_circuit_breaker

    cb = get_circuit_breaker()
    if not cb.can_execute():
        logger.debug("Embedding circuit breaker OPEN, returning zero vector")
        return _zero_vec()

    for attempt in range(_MAX_EMBED_RETRIES + 1):
        try:
            vec = await asyncio.wait_for(
                asyncio.to_thread(_embed_sync, text),
                timeout=_EMBED_TIMEOUT_SECONDS,
            )
            cb.record_success()
            return np.array(vec, dtype=np.float32)
        except asyncio.TimeoutError:
            cb.record_failure()
            if attempt < _MAX_EMBED_RETRIES:
                backoff = _RETRY_BACKOFF_BASE ** (attempt + 1)
                logger.warning(
                    f"Embedding timeout (attempt {attempt + 1}/{_MAX_EMBED_RETRIES + 1}), "
                    f"retrying in {backoff:.1f}s: {text[:50]!r}..."
                )
                await asyncio.sleep(backoff)
            else:
                logger.warning(f"Embedding timeout after all retries: {text[:50]!r}...")
        except Exception as e:
            cb.record_failure()
            if attempt < _MAX_EMBED_RETRIES:
                backoff = _RETRY_BACKOFF_BASE ** (attempt + 1)
                logger.warning(
                    f"Embedding error (attempt {attempt + 1}/{_MAX_EMBED_RETRIES + 1}), "
                    f"retrying in {backoff:.1f}s: {e}"
                )
                await asyncio.sleep(backoff)
            else:
                logger.error(f"Embedding permanently failed: {e}")

    return _zero_vec()


def _embed_sync(text: str) -> list[float]:
    """Synchronous single-text embedding (runs in thread pool)."""
    embed_model = get_embedding()
    return embed_model.get_text_embedding(text)


async def _embed_batch(texts: list[str]) -> list[np.ndarray]:
    """Async batch embed with timeout, retry, and circuit breaker.

    For large batches, splits into smaller chunks to avoid single-point failures.
    Returns list of float32 vectors (zero vectors on failure).
    """
    if not texts:
        return []

    from backend.embedding_tasks import get_circuit_breaker

    cb = get_circuit_breaker()
    if not cb.can_execute():
        logger.debug(f"Embedding circuit breaker OPEN, returning {len(texts)} zero vectors")
        return [_zero_vec() for _ in texts]

    # Split large batches into chunks of 10 to limit blast radius
    CHUNK_SIZE = 10
    if len(texts) > CHUNK_SIZE:
        results = []
        for i in range(0, len(texts), CHUNK_SIZE):
            chunk = texts[i:i + CHUNK_SIZE]
            chunk_results = await _embed_batch_chunk(chunk, cb)
            results.extend(chunk_results)
        return results

    return await _embed_batch_chunk(texts, cb)


async def _embed_batch_chunk(texts: list[str], cb) -> list[np.ndarray]:
    """Embed a single chunk with retry logic."""
    timeout = max(_EMBED_TIMEOUT_SECONDS, len(texts) * 3.0)

    for attempt in range(_MAX_EMBED_RETRIES + 1):
        try:
            vecs = await asyncio.wait_for(
                asyncio.to_thread(_embed_batch_sync, texts),
                timeout=timeout,
            )
            cb.record_success()
            return [np.array(v, dtype=np.float32) for v in vecs]
        except asyncio.TimeoutError:
            cb.record_failure()
            if attempt < _MAX_EMBED_RETRIES:
                backoff = _RETRY_BACKOFF_BASE ** (attempt + 1)
                logger.warning(
                    f"Batch embedding timeout ({len(texts)} texts, attempt {attempt + 1}), "
                    f"retrying in {backoff:.1f}s"
                )
                await asyncio.sleep(backoff)
            else:
                logger.warning(f"Batch embedding timeout after all retries ({len(texts)} texts)")
        except Exception as e:
            cb.record_failure()
            if attempt < _MAX_EMBED_RETRIES:
                backoff = _RETRY_BACKOFF_BASE ** (attempt + 1)
                logger.warning(
                    f"Batch embedding error (attempt {attempt + 1}), retrying in {backoff:.1f}s: {e}"
                )
                await asyncio.sleep(backoff)
            else:
                logger.error(f"Batch embedding permanently failed ({len(texts)} texts): {e}")

    return [_zero_vec() for _ in texts]


def _embed_batch_sync(texts: list[str]) -> list[list[float]]:
    """Synchronous batch embedding (runs in thread pool)."""
    embed_model = get_embedding()
    return embed_model.get_text_embedding_batch(texts)


# ── Sync-compatible wrappers (bridge async → sync for legacy callers) ──

def _run_async(coro):
    """Run an async coroutine in the existing event loop or a new one."""
    running_loop = None
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop and running_loop.is_running():
        # Already inside an event loop — use thread executor to avoid nesting.
        # No `with` block: __exit__ does shutdown(wait=True), which would block
        # on the worker until the coroutine truly finishes even after result()
        # raised TimeoutError — the caller would stall the full internal
        # retry-with-backoff duration (well past this deadline) and only then
        # get the timeout. shutdown(wait=False) abandons the worker instead.
        # The deadline covers the worst case (retries × per-call timeout +
        # backoff) so a successfully-degrading inner call isn't cut short.
        import concurrent.futures
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(asyncio.run, coro)
            deadline = (_MAX_EMBED_RETRIES + 1) * (_EMBED_TIMEOUT_SECONDS + _RETRY_BACKOFF_BASE ** _MAX_EMBED_RETRIES) + 5.0
            return future.result(timeout=deadline)
        finally:
            pool.shutdown(wait=False)
    else:
        return asyncio.run(coro)


def search_memory_sync(
    query: str,
    user_id: str,
    chunk_types: list[str] | None = None,
    topic: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """Sync wrapper for search_memory."""
    return _run_async(search_memory(query, user_id, chunk_types, topic, top_k))


def find_similar_weak_point_sync(
    new_point: str,
    existing_points: list[dict],
    user_id: str,
    threshold: float = SIMILARITY_THRESHOLD,
) -> int | None:
    """Sync wrapper for find_similar_weak_point."""
    return _run_async(find_similar_weak_point(new_point, existing_points, user_id, threshold))


def index_session_memory_sync(
    session_id: str | None,
    topic: str | None,
    summary: str,
    weak_points: list[dict],
    user_id: str,
    strong_points: list[dict] | None = None,
    insight_text: str = "",
):
    """Sync wrapper for index_session_memory."""
    return _run_async(index_session_memory(
        session_id=session_id, topic=topic, summary=summary,
        weak_points=weak_points, user_id=user_id,
        strong_points=strong_points, insight_text=insight_text,
    ))


def _time_decay(created_at: str) -> float:
    """Exponential time decay. Returns a multiplier in (0.7, 1.0].

    Blended as TIME_DECAY_WEIGHT * decay + (1 - TIME_DECAY_WEIGHT); with weight
    0.3 the oldest items retain ~70% weight (the design's max 30% reduction)."""
    try:
        age = (datetime.now() - datetime.fromisoformat(created_at)).total_seconds() / 86400
    except (ValueError, TypeError):
        return 1.0
    decay = 0.5 ** (max(age, 0) / TIME_DECAY_HALF_LIFE)
    # Blend: score * (weight * decay + (1 - weight))
    return TIME_DECAY_WEIGHT * decay + (1 - TIME_DECAY_WEIGHT)


# ── Write ──

async def index_session_memory(
    session_id: str | None,
    topic: str | None,
    summary: str,
    weak_points: list[dict],
    user_id: str,
    strong_points: list[dict] | None = None,
    insight_text: str = "",
):
    """Embed and store memory chunks for a completed session."""
    chunks: list[tuple[str, str, str | None, str | None, dict]] = []

    if summary:
        chunks.append(("session_summary", summary, topic, session_id, {}))

    for wp in weak_points:
        point = wp.get("point", wp) if isinstance(wp, dict) else str(wp)
        if point:
            wp_topic = wp.get("topic", topic) if isinstance(wp, dict) else topic
            chunks.append(("weak_point", point, wp_topic, session_id, {"topic": wp_topic}))

    if insight_text:
        chunks.append(("insight", insight_text[:2000], topic, session_id, {}))

    if not chunks:
        return

    # Async batch embed with timeout
    texts = [c[1] for c in chunks]
    vectors = await _embed_batch(texts)

    now = datetime.now().isoformat()
    # embedding 失败/熔断时 _embed_batch 会返回全零兜底向量；这些零向量不能当正常
    # record 入库，否则脏行会污染后续检索（且可能是错误维度）。写库前过滤掉全零项。
    records = [
        MemoryRecord(
            content=content, chunk_type=chunk_type, topic=t,
            session_id=sid, embedding=vec, created_at=now, metadata=meta,
        )
        for (chunk_type, content, t, sid, meta), vec in zip(chunks, vectors)
        if np.any(vec)
    ]
    if not records:
        logger.warning("index_session_memory: 所有向量均为兜底零向量，跳过写库。")
        return

    # Backend write + cleanup are sync — wrap in to_thread so the event loop
    # isn't blocked (the old inline conn.execute path did block).
    store = get_vector_store()
    await asyncio.to_thread(store.add, user_id, records)
    logger.info(f"Indexed {len(records)} memory chunks for session {session_id or 'unknown'}.")

    # Auto-cleanup oldest beyond the per-user cap.
    await asyncio.to_thread(store.cleanup_oldest, user_id, MAX_VECTORS_PER_USER)


# ── Read ──

async def search_memory(
    query: str,
    user_id: str,
    chunk_types: list[str] | None = None,
    topic: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """Semantic search with time decay. Returns [{content, chunk_type, topic, score, created_at}]."""
    # Embed query (async with timeout)
    query_vec = await _embed(query)
    store = get_vector_store()

    # Backend search returns raw cosine; we apply time decay + sort + top_k here
    # so numpy and qdrant produce identical final ordering. Pull the full filtered
    # set (limit=MAX_VECTORS_PER_USER) — decaying then truncating MUST happen after
    # retrieval, not before. Deserialize/cosine/decay/sort are CPU-bound over ≤500
    # rows → keep them off the event loop.
    def _search_and_rank() -> list[dict]:
        hits = store.search(
            user_id, query_vec,
            chunk_types=chunk_types, topic=topic, limit=MAX_VECTORS_PER_USER,
        )
        results = [
            {
                "content": h.content,
                "chunk_type": h.chunk_type,
                "topic": h.topic,
                "session_id": h.session_id,
                "score": h.score * _time_decay(h.created_at),
                "created_at": h.created_at,
            }
            for h in hits
        ]
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    return await asyncio.to_thread(_search_and_rank)


async def find_similar_weak_point(
    new_point: str,
    existing_points: list[dict],
    user_id: str,
    threshold: float = SIMILARITY_THRESHOLD,
) -> int | None:
    """Find index of most similar existing weak point via embedding similarity.
    Returns index into existing_points, or None if no match above threshold."""
    if not existing_points:
        return None

    # Pull cached weak_point embeddings from the store (content → embedding).
    # Sync backend call → to_thread so the event loop isn't blocked.
    store = get_vector_store()
    cached = await asyncio.to_thread(store.iter_embeddings, user_id, "weak_point")

    # Embed the new point (async with timeout)
    new_vec = await _embed(new_point)

    # Resolve a vector for every existing weak point — cache hits plus a single
    # batch embed for the misses — then do ONE matrix cosine + argmax, instead of
    # N reshape(1,-1) calls each recomputing new_vec's norm (cf. search_memory).
    indices: list[int] = []
    vectors: list = []
    missing_texts: list[str] = []
    missing_slots: list[int] = []

    for i, wp in enumerate(existing_points):
        point_text = wp.get("point", "") if isinstance(wp, dict) else str(wp)
        if not point_text:
            continue
        indices.append(i)
        # 缓存命中还需维度一致：换过 embedding 模型后旧缓存维度与 new_vec 不符，
        # 此时当作 miss 重嵌入 —— 既避免 np.stack 异维度崩溃，又顺带自愈缓存。
        if point_text in cached and cached[point_text].shape == new_vec.shape:
            vectors.append(cached[point_text])
        else:
            missing_slots.append(len(vectors))
            vectors.append(None)  # placeholder, filled after the batch embed
            missing_texts.append(point_text)

    # Embed uncached points once and write them back into `cached`, so a point
    # rewritten by an LLM UPDATE isn't re-embedded on every subsequent call.
    if missing_texts:
        embedded = await _embed_batch(missing_texts)
        for slot, text, vec in zip(missing_slots, missing_texts, embedded):
            vec_np = np.array(vec, dtype=np.float32)
            vectors[slot] = vec_np
            cached[text] = vec_np

    if not vectors:
        return None

    # 维度过滤要在 stack 之前：批量 embed 失败时返回的零向量维度走探测/默认值，
    # 可能与缓存向量不同——混合维度会让 np.stack 直接抛 ValueError（第 421 行的
    # 守卫在 stack 之后，protect 不到）。逐条剔除异维向量，保持 indices 对齐。
    aligned = [(idx, vec) for idx, vec in zip(indices, vectors) if vec.shape == new_vec.shape]
    if not aligned:
        logger.warning(
            "weak_point 去重维度不一致（全部候选与 new_vec %s 不符），跳过本次匹配。",
            new_vec.shape,
        )
        return None
    indices = [idx for idx, _ in aligned]
    matrix = np.stack([vec for _, vec in aligned])

    sims = _cosine_similarity(new_vec, matrix)
    best_pos = int(np.argmax(sims))
    if float(sims[best_pos]) >= threshold:
        return indices[best_pos]
    return None


# ── Maintenance ──

def rebuild_index_from_profile(user_id: str):
    """Rebuild weak_point vectors from current profile.json."""
    from backend.memory import _load_profile

    store = get_vector_store()
    store.delete_by_type(user_id, "weak_point")

    profile = _load_profile(user_id)
    weak_points = profile.get("weak_points", [])
    if not weak_points:
        return

    # Filter the wp dicts FIRST, then derive texts — zipping filtered texts
    # against the unfiltered weak_points list misaligns topic/created_at/
    # metadata for every record after a skipped (empty-point) entry.
    valid_wps = [wp for wp in weak_points if wp.get("point")]
    if not valid_wps:
        return
    texts = [wp["point"] for wp in valid_wps]

    embed_model = get_embedding()
    vectors = embed_model.get_text_embedding_batch(texts)
    now = datetime.now().isoformat()

    records = [
        MemoryRecord(
            content=text,
            chunk_type="weak_point",
            topic=wp.get("topic"),
            session_id=None,
            embedding=np.array(vec, dtype=np.float32),
            created_at=wp.get("first_seen", now),
            metadata={"topic": wp.get("topic", ""), "times_seen": wp.get("times_seen", 1)},
        )
        for text, vec, wp in zip(texts, vectors, valid_wps)
    ]
    store.add(user_id, records)
    logger.info(f"Rebuilt {len(records)} weak_point vectors for user {user_id}.")
