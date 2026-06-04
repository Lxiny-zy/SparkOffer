"""向量存储工厂 —— 按配置返回 numpy（默认）或 qdrant 后端，单例 + 自动降级。

仿 llm_provider.get_embedding 的「单例 + get_config_version 版本缓存」模式：
配置热更新（channel 变动等）会 bump version，触发下次取用时重建。
Qdrant 初始化失败时优雅降级回 numpy，保证存储层永远可用 —— 这与项目
「不引入会让主流程崩溃的硬依赖」的取向一致。
"""
import logging

from backend.ai_config import get_config_version
from backend.config import settings
from backend.vector_store.base import (
    MAX_VECTORS_PER_USER,
    AbstractVectorStore,
    MemoryRecord,
    SearchHit,
)
from backend.vector_store.numpy_store import NumpyVectorStore

logger = logging.getLogger("uvicorn")

_store_instance: AbstractVectorStore | None = None
_store_config_version = -1


def get_vector_store() -> AbstractVectorStore:
    """记忆库向量存储后端（单例，配置变更时自动重建）。"""
    global _store_instance, _store_config_version
    ver = get_config_version()
    if _store_instance is None or _store_config_version != ver:
        _store_instance = _create_vector_store()
        _store_config_version = ver
    return _store_instance


def _create_vector_store() -> AbstractVectorStore:
    mode = settings.vector_backend_mode()
    if mode == "qdrant":
        # 惰性 import：只在真正启用 qdrant 时才依赖 qdrant_store / qdrant-client。
        # 若该模块/依赖缺失或连接失败，下面的 except 会降级回 numpy。
        try:
            from backend.vector_store.qdrant_store import QdrantVectorStore
            store = QdrantVectorStore(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key or None,
                collection_name=settings.qdrant_memory_collection,
            )
            logger.info(
                "VectorStore backend: qdrant (collection=%s)",
                settings.qdrant_memory_collection,
            )
            return store
        except Exception as e:
            logger.warning("Qdrant init failed, degrading to numpy: %s", e)
            return NumpyVectorStore()
    logger.info("VectorStore backend: numpy")
    return NumpyVectorStore()


def reset_vector_store() -> None:
    """强制下次 get_vector_store 重建（供 llm_provider.invalidate_singletons 调用）。"""
    global _store_instance, _store_config_version
    _store_instance = None
    _store_config_version = -1


__all__ = [
    "get_vector_store",
    "reset_vector_store",
    "AbstractVectorStore",
    "MemoryRecord",
    "SearchHit",
    "MAX_VECTORS_PER_USER",
]
