"""向量存储抽象层 —— 接口与数据模型。

定义 AbstractVectorStore，让记忆库的向量存储后端可插拔：
- NumpyVectorStore：SQLite BLOB + numpy 暴力余弦（默认，适配 ≤500 向量/用户的项目规模）
- QdrantVectorStore：Qdrant 工程化向量库（可选，配置 VECTOR_BACKEND=qdrant 启用）

设计要点：
- 接口以「向量」为输入/存储单元 —— embedding 调用与时间衰减留在应用层
  (backend/vector_memory.py)，两个后端共享，保证行为一致。
- search() 返回原始 cosine 分数（不含时间衰减）；衰减、排序、top_k 截断
  由调用方统一后处理，使 numpy 与 qdrant 的最终结果逐条等价。
- 所有方法为同步 —— 由 vector_memory.py 的 async 层用 asyncio.to_thread 包裹
  （沿用项目既有的「同步 IO + to_thread」模式，cf. vector_memory.py:345）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

# 单用户向量数上限 —— 超过则 cleanup_oldest 删最旧的。这是存储层的容量约束，
# 故归属在此；vector_memory.py 从这里 re-export 以保持向后兼容的引用。
MAX_VECTORS_PER_USER = 500


# ── 底层向量工具（被各后端与 graph.py / 迁移脚本共享） ──
# 定义在 base 而非 vector_memory，是为了打破 import 环：
#   vector_memory → vector_store.__init__ → numpy_store → base  （单向）
# 同时 vector_memory 从这里 re-export 这三个名字，保持 graph.py /
# rag_retrieval.py 既有的 `from backend.vector_memory import ...` 不变。

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


@dataclass
class MemoryRecord:
    """一条待写入的记忆向量（embedding 已由应用层算好）。"""
    content: str
    chunk_type: str            # "session_summary" | "weak_point" | "insight"
    topic: str | None
    session_id: str | None
    embedding: np.ndarray
    created_at: str            # ISO8601
    metadata: dict = field(default_factory=dict)


@dataclass
class SearchHit:
    """一条检索命中。score 是原始 cosine（时间衰减由调用方应用）。"""
    content: str
    chunk_type: str
    topic: str | None
    session_id: str | None
    score: float
    created_at: str


class AbstractVectorStore(ABC):
    """记忆库向量存储后端接口。

    实现需覆盖记忆库的全部存取需求。薄弱点去重与题目去重（KV 缓存性质，
    非 ANN 检索）不在此接口职责内 —— 它们保留在 SQLite。
    """

    #: 后端标识，供日志/timeline 观测。子类覆盖为 "numpy" / "qdrant"。
    backend: str = "abstract"

    @abstractmethod
    def add(self, user_id: str, records: list[MemoryRecord]) -> None:
        """批量写入记忆向量。"""

    @abstractmethod
    def search(
        self,
        user_id: str,
        query_vec: np.ndarray,
        *,
        chunk_types: list[str] | None = None,
        topic: str | None = None,
        limit: int = MAX_VECTORS_PER_USER,
    ) -> list[SearchHit]:
        """语义检索。返回原始 cosine 分（降序），不做时间衰减。

        limit 默认取 MAX_VECTORS_PER_USER：调用方需要全量命中，以便在应用层
        做时间衰减后再截 top_k —— 这样 numpy 与 qdrant 的最终排序逐条一致
        （不能让后端先按裸 cosine 截断，否则衰减后的排序会偏离）。
        """

    @abstractmethod
    def delete_by_type(self, user_id: str, chunk_type: str) -> None:
        """删除某用户某类型的全部向量（rebuild_index_from_profile 用）。"""

    @abstractmethod
    def cleanup_oldest(self, user_id: str, max_count: int) -> None:
        """当用户向量数超过 max_count 时，删除最旧的多余项。"""

    @abstractmethod
    def count(self, user_id: str) -> int:
        """返回某用户的向量总数。"""

    @abstractmethod
    def iter_embeddings(self, user_id: str, chunk_type: str) -> dict[str, np.ndarray]:
        """返回某用户某类型的 {content: embedding} 映射。

        find_similar_weak_point 把它当 embedding 缓存用 —— 避免对 profile.json
        里已存的薄弱点重复 embed。
        """
