"""QdrantVectorStore —— 工程化向量库后端（可选，VECTOR_BACKEND=qdrant 启用）。

与 NumpyVectorStore 行为对齐，但底层是 Qdrant：

- 多租户：单 collection + payload 过滤（user_id/chunk_type/topic），而非每用户
  一个 collection。这是 Qdrant 官方推荐的 multitenancy，避免 collection 数量爆炸。
- Point ID：确定性 UUID5（uuid5(NS, user_id|session_id|chunk_type|content)）。
  Qdrant 只接受 uint64 / UUID，且会把 MD5 hex 误判为 UUID —— 显式 uuid5 既合规又
  保证 upsert 幂等（迁移脚本可重跑不产生重复）。原始字段全部进 payload。
- 维度：collection 的向量维度必须固定。本项目 embedding 维度随模型变
  （bge-m3=1024 / OpenAI=1536），故 **不探测当前模型**，而是首次 add() 时直接用
  写入向量自身的维度建表 —— 这样维度永远与实际数据一致，且省去一次 embedding 调用。
  若运行期换了 embedding 模型导致维度变化：add() 会检测到 collection 维度与新向量
  不符并抛清晰错误（提示重跑迁移）；search() 则返回空（降级为「本回合无记忆」），
  二者都不会把脏维度数据写坏或抛出难懂的 Qdrant 400。
- 时间衰减不在此做：search() 返回原始 cosine（query_points 的 score），由
  vector_memory.search_memory 统一做衰减，保证与 numpy 后端逐条等价。
- 同步 client：所有方法同步，由 vector_memory 的 async 层 asyncio.to_thread 包裹。
"""
from __future__ import annotations

import logging
import threading
import uuid

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from backend.vector_store.base import (
    MAX_VECTORS_PER_USER,
    AbstractVectorStore,
    MemoryRecord,
    SearchHit,
)

logger = logging.getLogger("uvicorn")

# 固定 namespace —— 与迁移脚本共用 make_point_id，保证运行时写入与存量迁移
# 对同一条记忆生成相同的 point id（幂等）。
_NAMESPACE = uuid.UUID("a3f1e2d4-5b6c-7890-abcd-ef0123456789")

# scroll 分页批大小。
_SCROLL_BATCH = 256


def make_point_id(user_id: str, session_id: str | None, chunk_type: str, content: str) -> str:
    """确定性 point id。迁移脚本与运行时写入必须用同一函数以保持幂等。"""
    return str(uuid.uuid5(_NAMESPACE, f"{user_id}|{session_id or ''}|{chunk_type}|{content}"))


class QdrantVectorStore(AbstractVectorStore):
    """记忆库的 Qdrant 后端。单 collection + payload 多租户。"""

    backend = "qdrant"

    def __init__(self, url: str, api_key: str | None, collection_name: str):
        self.collection_name = collection_name
        self.client = QdrantClient(url=url, api_key=api_key, timeout=10.0)
        # 连通性探针：连不上 / 鉴权失败会抛异常，工厂据此降级回 numpy。
        self.client.get_collections()
        self._exists_cached = False      # collection 是否已确认存在（True 后不再查）
        self._dim: int | None = None     # collection 的向量维度（确认存在后缓存）
        self._lock = threading.Lock()

    # ── collection 存在性 / 维度（懒探测 + 缓存） ──

    def _exists(self) -> bool:
        """collection 是否存在。一旦确认存在即缓存，避免每次操作都查。"""
        if self._exists_cached:
            return True
        if self.client.collection_exists(self.collection_name):
            self._exists_cached = True
            return True
        return False

    def _collection_dim(self) -> int | None:
        """collection 的向量维度；不存在返回 None。确认后缓存。"""
        if self._dim is not None:
            return self._dim
        if not self._exists():
            return None
        vectors = self.client.get_collection(self.collection_name).config.params.vectors
        # 单 unnamed vector → VectorParams.size；防御性兼容 named-vector dict。
        size = vectors.size if hasattr(vectors, "size") else next(iter(vectors.values())).size
        self._dim = int(size)
        return self._dim

    def _ensure_collection(self, dim: int) -> None:
        """确保 collection 存在并按 `dim` 建表。仅由 add() 调用（此时有真实向量维度）。"""
        if self._exists():
            return
        with self._lock:
            if self._exists():  # double-checked：避免多线程并发重复建表
                return
            self.client.create_collection(
                self.collection_name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            # 在过滤字段上建 payload index，加速 multitenant 检索。
            for field in ("user_id", "chunk_type", "topic"):
                try:
                    self.client.create_payload_index(
                        self.collection_name, field, PayloadSchemaType.KEYWORD,
                    )
                except Exception as e:  # index 已存在等非致命错误
                    logger.debug("create_payload_index(%s) skipped: %s", field, e)
            self._dim = dim
            self._exists_cached = True
            logger.info(
                "Qdrant collection '%s' created (dim=%d, cosine)",
                self.collection_name, dim,
            )

    @staticmethod
    def _user_filter(user_id: str, chunk_type: str | None = None,
                     chunk_types: list[str] | None = None, topic: str | None = None) -> Filter:
        must = [FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        if chunk_type is not None:
            must.append(FieldCondition(key="chunk_type", match=MatchValue(value=chunk_type)))
        if chunk_types:
            must.append(FieldCondition(key="chunk_type", match=MatchAny(any=list(chunk_types))))
        if topic:
            must.append(FieldCondition(key="topic", match=MatchValue(value=topic)))
        return Filter(must=must)

    # ── Write ──

    def add(self, user_id: str, records: list[MemoryRecord]) -> None:
        if not records:
            return
        dim = len(records[0].embedding)
        self._ensure_collection(dim)
        # 维度守卫：collection 已存在但维度与待写向量不符 —— 多半是换过 embedding
        # 模型。此时静默 upsert 会被 Qdrant 拒绝（难懂的 400），故主动抛清晰错误。
        existing = self._collection_dim()
        if existing is not None and existing != dim:
            raise RuntimeError(
                f"Qdrant collection '{self.collection_name}' 维度={existing} 与待写 embedding "
                f"维度={dim} 不一致（embedding 模型可能已变更）。请删除该 collection 后重新生成"
                f"向量，或运行 `python -m scripts.migrate_memory_to_qdrant --recreate` 重建。"
            )
        points = [
            PointStruct(
                id=make_point_id(user_id, r.session_id, r.chunk_type, r.content),
                vector=r.embedding.astype(np.float32).tolist(),
                payload={
                    "user_id": user_id,
                    "chunk_type": r.chunk_type,
                    "topic": r.topic,
                    "session_id": r.session_id,
                    "content": r.content,
                    "created_at": r.created_at,
                    # 额外 meta（如 times_seen）合并进 payload；不覆盖上面的核心字段。
                    **{k: v for k, v in (r.metadata or {}).items()
                       if k not in {"user_id", "chunk_type", "topic", "session_id", "content", "created_at"}},
                },
            )
            for r in records
        ]
        self.client.upsert(self.collection_name, points=points)

    # ── Read ──

    def search(
        self,
        user_id: str,
        query_vec: np.ndarray,
        *,
        chunk_types: list[str] | None = None,
        topic: str | None = None,
        limit: int = MAX_VECTORS_PER_USER,
    ) -> list[SearchHit]:
        # query embedding 失败会退化成零向量；Qdrant 对零范数向量会报错，而 numpy
        # 后端此时返回的全 0 分在上层 score>阈值 处也会被滤掉 —— 故直接返回空，
        # 这是 embedding 已降级时的合理行为。
        if not np.any(query_vec):
            return []
        dim = self._collection_dim()
        if dim is None:
            return []  # collection 还不存在 → 该用户尚无记忆
        if dim != len(query_vec):
            # 维度不符（换过 embedding 模型）：降级为「本回合无记忆」而非崩溃。
            logger.warning(
                "Qdrant '%s' 维度=%d 与查询向量维度=%d 不一致（embedding 模型变更?），返回空。",
                self.collection_name, dim, len(query_vec),
            )
            return []
        resp = self.client.query_points(
            self.collection_name,
            query=query_vec.astype(np.float32).tolist(),
            query_filter=self._user_filter(user_id, chunk_types=chunk_types, topic=topic),
            limit=limit,
            with_payload=True,
        )
        hits: list[SearchHit] = []
        for p in resp.points:
            pl = p.payload or {}
            hits.append(SearchHit(
                content=pl.get("content", ""),
                chunk_type=pl.get("chunk_type", ""),
                topic=pl.get("topic"),
                session_id=pl.get("session_id"),
                score=float(p.score),
                created_at=pl.get("created_at", ""),
            ))
        return hits

    def iter_embeddings(self, user_id: str, chunk_type: str) -> dict[str, np.ndarray]:
        if not self._exists():
            return {}
        out: dict[str, np.ndarray] = {}
        offset = None
        flt = self._user_filter(user_id, chunk_type=chunk_type)
        while True:
            batch, offset = self.client.scroll(
                self.collection_name,
                scroll_filter=flt,
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=["content"],
                with_vectors=True,
            )
            for p in batch:
                content = (p.payload or {}).get("content")
                if content is not None and p.vector is not None:
                    out[content] = np.array(p.vector, dtype=np.float32)
            if offset is None:
                break
        return out

    def count(self, user_id: str) -> int:
        if not self._exists():
            return 0
        return self.client.count(
            self.collection_name,
            count_filter=self._user_filter(user_id),
            exact=True,
        ).count

    # ── Maintenance ──

    def delete_by_type(self, user_id: str, chunk_type: str) -> None:
        if not self._exists():
            return
        self.client.delete(
            self.collection_name,
            points_selector=FilterSelector(filter=self._user_filter(user_id, chunk_type=chunk_type)),
        )

    def cleanup_oldest(self, user_id: str, max_count: int) -> None:
        # Qdrant 无「按字段排序删除」的原生 API：scroll 出该用户全部点的 created_at，
        # 在客户端排序后删掉最旧的多余项（与 numpy 后端按 id 删最旧语义一致）。
        if not self._exists():
            return
        points = []
        offset = None
        flt = self._user_filter(user_id)
        while True:
            batch, offset = self.client.scroll(
                self.collection_name,
                scroll_filter=flt,
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=["created_at"],
                with_vectors=False,
            )
            points.extend(batch)
            if offset is None:
                break
        if len(points) <= max_count:
            return
        # 按 created_at 升序删最旧（与 NumpyVectorStore 一致）；point id 作确定性
        # tie-break，避免 created_at 相同（同批写入）时 scroll 顺序导致的不确定淘汰。
        points.sort(key=lambda p: ((p.payload or {}).get("created_at", ""), str(p.id)))
        to_delete = [p.id for p in points[: len(points) - max_count]]
        if to_delete:
            self.client.delete(
                self.collection_name,
                points_selector=PointIdsList(points=to_delete),
            )
