"""NumpyVectorStore —— SQLite BLOB + numpy 暴力余弦的默认后端。

检索/衰减逻辑取自 vector_memory.py 的自建向量库实现。写入与淘汰语义与
QdrantVectorStore 对齐：add() 按 (user_id|session_id|chunk_type|content) 幂等
（重复写入覆盖而非堆叠重复行），cleanup_oldest() 按 created_at 淘汰最旧。
适配 ≤500 向量/用户 的项目规模（sub-ms 检索）。

所有方法为同步 —— SQLite 的线程本地连接 (get_db) 配合调用方的
asyncio.to_thread 使用，不阻塞事件循环。
"""
from __future__ import annotations

import json
import logging

import numpy as np

from backend.storage.database import get_db
from backend.vector_store.base import (
    MAX_VECTORS_PER_USER,
    AbstractVectorStore,
    MemoryRecord,
    SearchHit,
    _cosine_similarity,
    _deserialize,
    _serialize,
)


logger = logging.getLogger("uvicorn")


class NumpyVectorStore(AbstractVectorStore):
    """SQLite `memory_vectors` 表 + numpy 余弦。无外部依赖、零运维。"""

    backend = "numpy"

    def add(self, user_id: str, records: list[MemoryRecord]) -> None:
        if not records:
            return
        conn = get_db()
        for r in records:
            # Idempotent on the same identity tuple QdrantVectorStore keys its
            # point id on (user_id|session_id|chunk_type|content): delete any
            # existing row first so a re-indexed session doesn't accumulate
            # duplicate vectors here while the qdrant backend stays deduped.
            conn.execute(
                "DELETE FROM memory_vectors WHERE user_id = ? AND chunk_type = ? "
                "AND content = ? AND COALESCE(session_id, '') = COALESCE(?, '')",
                (user_id, r.chunk_type, r.content, r.session_id),
            )
            conn.execute(
                "INSERT INTO memory_vectors "
                "(chunk_type, content, topic, session_id, metadata, embedding, user_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.chunk_type, r.content, r.topic, r.session_id,
                    json.dumps(r.metadata or {}, ensure_ascii=False),
                    _serialize(r.embedding), user_id, r.created_at,
                ),
            )
        conn.commit()

    def search(
        self,
        user_id: str,
        query_vec: np.ndarray,
        *,
        chunk_types: list[str] | None = None,
        topic: str | None = None,
        limit: int = MAX_VECTORS_PER_USER,
    ) -> list[SearchHit]:
        # 纯字符串拼装的过滤条件 —— 与重构前 search_memory 的 WHERE 子句一致。
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

        rows = get_db().execute(
            f"SELECT chunk_type, content, topic, session_id, embedding, created_at "
            f"FROM memory_vectors{where_clause}",
            params,
        ).fetchall()
        if not rows:
            return []

        # 维度守卫：换过 embedding 模型后表里会同时存在新旧维度的向量，直接
        # np.stack 异维度数组会抛 ValueError。这里只保留与查询向量同维度的行
        # （旧维度向量在新模型下本就无法比较），避免崩溃 —— 与 qdrant 后端的
        # 「维度不符则跳过」行为一致。
        qdim = query_vec.shape[0]
        kept = [(_deserialize(r["embedding"]), r) for r in rows]
        kept = [(v, r) for v, r in kept if v.shape[0] == qdim]
        if len(kept) < len(rows):
            logger.warning(
                "NumpyVectorStore: 跳过 %d/%d 条维度不匹配的向量（embedding 模型变更?）。",
                len(rows) - len(kept), len(rows),
            )
        if not kept:
            return []

        embeddings = np.stack([v for v, _ in kept])
        sims = _cosine_similarity(query_vec, embeddings)
        hits = [
            SearchHit(
                content=r["content"], chunk_type=r["chunk_type"], topic=r["topic"],
                session_id=r["session_id"], score=float(sims[i]), created_at=r["created_at"],
            )
            for i, (_, r) in enumerate(kept)
        ]
        # 按裸 cosine 降序，截 limit。limit 默认是全量，最终的时间衰减 + top_k
        # 由调用方做（见 base.search 的契约）。
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def delete_by_type(self, user_id: str, chunk_type: str) -> None:
        conn = get_db()
        conn.execute(
            "DELETE FROM memory_vectors WHERE chunk_type = ? AND user_id = ?",
            (chunk_type, user_id),
        )
        conn.commit()

    def cleanup_oldest(self, user_id: str, max_count: int) -> None:
        conn = get_db()
        count = conn.execute(
            "SELECT COUNT(*) FROM memory_vectors WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        if count <= max_count:
            return
        # 保留 created_at 最新的 max_count 条（id 仅做确定性 tie-break），删其余。
        # 以 created_at 为主序与 QdrantVectorStore.cleanup_oldest 对齐，使两个后端
        # 越过上限时淘汰同一批记录（qdrant 无插入自增 id，只能按 created_at）。
        conn.execute(
            "DELETE FROM memory_vectors WHERE user_id = ? AND id NOT IN ("
            "  SELECT id FROM memory_vectors WHERE user_id = ? "
            "  ORDER BY created_at DESC, id DESC LIMIT ?"
            ")",
            (user_id, user_id, max_count),
        )
        conn.commit()

    def count(self, user_id: str) -> int:
        return get_db().execute(
            "SELECT COUNT(*) FROM memory_vectors WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

    def iter_embeddings(self, user_id: str, chunk_type: str) -> dict[str, np.ndarray]:
        rows = get_db().execute(
            "SELECT content, embedding FROM memory_vectors WHERE chunk_type = ? AND user_id = ?",
            (chunk_type, user_id),
        ).fetchall()
        return {r["content"]: _deserialize(r["embedding"]) for r in rows}
