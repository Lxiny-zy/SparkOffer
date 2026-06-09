"""把 SQLite memory_vectors 的存量记忆向量迁移到 Qdrant。

仅迁移「记忆库」(memory_vectors 表)；薄弱点去重 / 题目去重的缓存按设计保留在
SQLite，不迁移。

用法（仓库根目录，用 -m 让 backend.* 可解析）：
    python -m scripts.migrate_memory_to_qdrant                 # 迁移全部用户
    python -m scripts.migrate_memory_to_qdrant --user <uid>    # 仅某用户
    python -m scripts.migrate_memory_to_qdrant --recreate      # 先删 collection 重建

幂等：point id 由 QdrantVectorStore.make_point_id 确定性生成，重跑不产生重复。
前提：.env 配好 QDRANT_URL（脚本据此连 Qdrant）。

注意：collection 维度按「存量向量的实际维度」创建，故迁移假设 embedding 模型未变。
若中途换过 embedding 模型导致维度变化，应重新生成向量而非迁移旧向量。
"""
from __future__ import annotations

import sys as _sys
# Windows 控制台默认 GBK，强制 UTF-8 以正常打印中文路径/统计。
try:
    _sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

import argparse
import json
import sys
from collections import defaultdict

from backend.config import settings
from backend.storage.database import get_db
from backend.vector_store.base import MemoryRecord, _deserialize
from backend.vector_store.qdrant_store import QdrantVectorStore

_COLS = "chunk_type, content, topic, session_id, metadata, embedding, user_id, created_at"


def _load_rows(user: str | None):
    conn = get_db()
    if user:
        return conn.execute(
            f"SELECT {_COLS} FROM memory_vectors WHERE user_id = ?", (user,),
        ).fetchall()
    return conn.execute(f"SELECT {_COLS} FROM memory_vectors").fetchall()


def _to_record(row) -> MemoryRecord:
    try:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
    except (json.JSONDecodeError, TypeError):
        meta = {}
    return MemoryRecord(
        content=row["content"],
        chunk_type=row["chunk_type"],
        topic=row["topic"],
        session_id=row["session_id"],
        embedding=_deserialize(row["embedding"]),
        created_at=row["created_at"],
        metadata=meta,
    )


def main():
    parser = argparse.ArgumentParser(description="Migrate memory_vectors → Qdrant.")
    parser.add_argument("--user", help="只迁移指定 user_id（默认全部）")
    parser.add_argument("--recreate", action="store_true", help="先删除并重建 collection")
    parser.add_argument("--collection", help="覆盖 collection 名（默认 settings.qdrant_memory_collection）")
    args = parser.parse_args()

    if not settings.qdrant_url:
        print("ERROR: QDRANT_URL 未配置（检查 .env）。", file=sys.stderr)
        sys.exit(1)

    collection = args.collection or settings.qdrant_memory_collection
    try:
        store = QdrantVectorStore(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            collection_name=collection,
        )
    except Exception as e:
        print(f"ERROR: 无法连接 Qdrant（{settings.qdrant_url}）：{e}", file=sys.stderr)
        sys.exit(1)

    rows = _load_rows(args.user)
    if not rows:
        print("没有可迁移的向量。")
        return

    if args.recreate and store.client.collection_exists(collection):
        store.client.delete_collection(collection)
        print(f"已删除原 collection '{collection}'。")

    # 用存量向量的实际维度建 collection（而非探测当前 embedding 模型），保证维度匹配。
    if not store.client.collection_exists(collection):
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams
        dim = len(_deserialize(rows[0]["embedding"]))
        store.client.create_collection(
            collection, vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        for field in ("user_id", "chunk_type", "topic"):
            try:
                store.client.create_payload_index(collection, field, PayloadSchemaType.KEYWORD)
            except Exception:
                pass
        print(f"已创建 collection '{collection}'（dim={dim}, cosine）。")

    # 按 user 分组，复用 store.add（collection 已存在 → 直接 upsert；point id 幂等）。
    by_user: dict[str, list[MemoryRecord]] = defaultdict(list)
    skipped = 0
    for row in rows:
        if not row["user_id"]:
            skipped += 1  # user_id 为空的旧数据在新检索逻辑下匹配不到，跳过
            continue
        by_user[row["user_id"]].append(_to_record(row))

    total = 0
    for uid, records in by_user.items():
        store.add(uid, records)
        total += len(records)
        print(f"  user={uid}: {len(records)} 条")

    print(f"\n迁移完成：{total} 条向量 → Qdrant collection '{collection}'（{len(by_user)} 个用户）。")
    if skipped:
        print(f"（跳过 {skipped} 条 user_id 为空的旧数据。）")


if __name__ == "__main__":
    main()
