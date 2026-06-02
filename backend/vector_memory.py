"""向量记忆系统 — 语义检索 + 时间衰减 + 薄弱点语义去重。
设计：- SQLite BLOB 存 float32 embedding
- numpy cosine similarity 搜索（百级向量，sub-ms）- profile.json 仍是真相源，向量索引是加速层
"""
import asyncio
import json
import logging
from datetime import datetime

import numpy as np

from backend.llm_provider import get_embedding
from backend.storage.database import get_db

logger = logging.getLogger("uvicorn")

# Embedding 超时配置（秒）
_EMBED_TIMEOUT_SECONDS = 30.0

SIMILARITY_THRESHOLD = 0.75  # weak point dedup
TIME_DECAY_HALF_LIFE = 14.0  # days
TIME_DECAY_WEIGHT = 0.3      # max 30% score reduction from age
MAX_VECTORS_PER_USER = 500   # auto-cleanup threshold


# ── Embedding helpers ──

_MAX_EMBED_RETRIES = 2  # Retry count for transient failures
_RETRY_BACKOFF_BASE = 1.5  # Exponential backoff base (seconds)


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
        return np.zeros(1536, dtype=np.float32)

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

    return np.zeros(1536, dtype=np.float32)


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
        return [np.zeros(1536, dtype=np.float32) for _ in texts]

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

    return [np.zeros(1536, dtype=np.float32) for _ in texts]


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
        # Already inside an event loop — use thread executor to avoid nesting
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=_EMBED_TIMEOUT_SECONDS + 5.0)
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


def _serialize(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def _deserialize(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Vectorized cosine similarity. query_vec: (D,), matrix: (N, D) → (N,)."""
    query_norm = np.linalg.norm(query_vec)
    if query_norm < 1e-10:
        return np.zeros(matrix.shape[0])
    row_norms = np.linalg.norm(matrix, axis=1)
    row_norms = np.clip(row_norms, 1e-10, None)
    return (matrix @ query_vec) / (row_norms * query_norm)


def _time_decay(created_at: str) -> float:
    """Exponential time decay. Returns multiplier in [0.5, 1.0] range."""
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
    conn = get_db()
    chunks = []

    if summary:
        chunks.append(("session_summary", summary, topic, session_id, "{}"))

    for wp in weak_points:
        point = wp.get("point", wp) if isinstance(wp, dict) else str(wp)
        if point:
            meta = json.dumps({"topic": wp.get("topic", topic) if isinstance(wp, dict) else topic})
            chunks.append(("weak_point", point, wp.get("topic", topic) if isinstance(wp, dict) else topic, session_id, meta))

    if insight_text:
        chunks.append(("insight", insight_text[:2000], topic, session_id, "{}"))

    if not chunks:
        return

    # Async batch embed with timeout
    texts = [c[1] for c in chunks]
    vectors = await _embed_batch(texts)

    now = datetime.now().isoformat()
    for (chunk_type, content, t, sid, meta), vec in zip(chunks, vectors):
        blob = _serialize(vec)
        conn.execute(
            "INSERT INTO memory_vectors (chunk_type, content, topic, session_id, metadata, embedding, user_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chunk_type, content, t, sid, meta, blob, user_id, now),
        )

    conn.commit()
    logger.info(f"Indexed {len(chunks)} memory chunks for session {session_id or 'unknown'}.")

    # Auto-cleanup
    _cleanup_old_vectors(user_id)


def _cleanup_old_vectors(user_id: str, max_count: int = MAX_VECTORS_PER_USER):
    """Delete oldest vectors when a user exceeds the max count."""
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM memory_vectors WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    if count <= max_count:
        return
    # Delete the oldest entries beyond the limit
    conn.execute(
        "DELETE FROM memory_vectors WHERE user_id = ? AND id NOT IN ("
        "  SELECT id FROM memory_vectors WHERE user_id = ? ORDER BY id DESC LIMIT ?"
        ")",
        (user_id, user_id, max_count),
    )
    conn.commit()
    logger.info(f"Cleaned up vectors for user {user_id}: {count} → {max_count}")


# ── Read ──

async def search_memory(
    query: str,
    user_id: str,
    chunk_types: list[str] | None = None,
    topic: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """Semantic search with time decay. Returns [{content, chunk_type, topic, score, created_at}]."""
    # Build filter query (pure string assembly — cheap, stays on the loop)
    where = ["user_id = ?"]
    params: list = [user_id]
    if chunk_types:
        placeholders = ",".join("?" for _ in chunk_types)
        where.append(f"chunk_type IN ({placeholders})")
        params.extend(chunk_types)
    if topic:
        where.append("topic = ?")
        params.append(topic)
    where_clause = " WHERE " + " AND ".join(where)

    # SQLite read (up to MAX_VECTORS_PER_USER BLOB rows) is sync — run it in a
    # worker thread so the event loop isn't blocked. get_db() returns this
    # thread's own connection (connections are thread-local).
    def _query_db():
        return get_db().execute(
            f"SELECT id, chunk_type, content, topic, session_id, embedding, created_at FROM memory_vectors{where_clause}",
            params,
        ).fetchall()

    rows = await asyncio.to_thread(_query_db)
    if not rows:
        return []

    # Embed query (async with timeout)
    query_vec = await _embed(query)

    # Deserialize + stack + cosine + time-decay + sort are all CPU-bound over up
    # to 500 rows — keep them off the event loop too.
    def _rank() -> list[dict]:
        embeddings = np.stack([_deserialize(r["embedding"]) for r in rows])
        similarities = _cosine_similarity(query_vec, embeddings)
        results = []
        for i, row in enumerate(rows):
            decay = _time_decay(row["created_at"])
            score = float(similarities[i]) * decay
            results.append({
                "content": row["content"],
                "chunk_type": row["chunk_type"],
                "topic": row["topic"],
                "session_id": row["session_id"],
                "score": score,
                "created_at": row["created_at"],
            })
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    return await asyncio.to_thread(_rank)


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

    conn = get_db()
    rows = conn.execute(
        "SELECT content, embedding FROM memory_vectors WHERE chunk_type = 'weak_point' AND user_id = ?",
        (user_id,),
    ).fetchall()

    # Build lookup: content → embedding
    cached = {}
    for r in rows:
        cached[r["content"]] = _deserialize(r["embedding"])

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
        if point_text in cached:
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

    sims = _cosine_similarity(new_vec, np.stack(vectors))
    best_pos = int(np.argmax(sims))
    if float(sims[best_pos]) >= threshold:
        return indices[best_pos]
    return None


# ── Maintenance ──

def rebuild_index_from_profile(user_id: str):
    """Rebuild weak_point vectors from current profile.json."""
    from backend.memory import _load_profile

    conn = get_db()
    conn.execute("DELETE FROM memory_vectors WHERE chunk_type = 'weak_point' AND user_id = ?", (user_id,))
    conn.commit()

    profile = _load_profile(user_id)
    weak_points = profile.get("weak_points", [])

    if not weak_points:
        return

    embed_model = get_embedding()
    texts = [wp["point"] for wp in weak_points if wp.get("point")]
    if not texts:
        return

    vectors = embed_model.get_text_embedding_batch(texts)
    now = datetime.now().isoformat()

    for text, vec, wp in zip(texts, vectors, weak_points):
        blob = _serialize(np.array(vec, dtype=np.float32))
        meta = json.dumps({"topic": wp.get("topic", ""), "times_seen": wp.get("times_seen", 1)})
        conn.execute(
            "INSERT INTO memory_vectors (chunk_type, content, topic, metadata, embedding, user_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("weak_point", text, wp.get("topic"), meta, blob, user_id, wp.get("first_seen", now)),
        )

    conn.commit()
    logger.info(f"Rebuilt {len(texts)} weak_point vectors for user {user_id}.")
