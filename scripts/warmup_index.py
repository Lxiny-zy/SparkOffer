#!/usr/bin/env python3
"""
知识库索引预热脚本

用途：在服务器上预先构建所有知识库主题的向量索引缓存，
     避免用户首次出题时现场调用 embedding API 导致超时。

使用方式（在后端 Docker 容器内执行）：
    docker exec -it <backend容器名> python /app/scripts/warmup_index.py

可选参数：
    --user-id   指定用户ID（默认自动从数据库读取第一个用户）
    --topic     只预热指定 topic（如 python），不传则预热全部
    --force     强制重建已存在的索引缓存

示例：
    # 预热所有 topic
    docker exec -it tech-backend-1 python /app/scripts/warmup_index.py

    # 只预热 python 这一个 topic
    docker exec -it tech-backend-1 python /app/scripts/warmup_index.py --topic python

    # 强制重建某 topic 的索引
    docker exec -it tech-backend-1 python /app/scripts/warmup_index.py --topic python --force
"""

import sys
import os
import argparse
import time
import sqlite3

# 确保能找到项目包
sys.path.insert(0, "/app")
os.chdir("/app")


def get_first_user_id() -> str | None:
    """从数据库读取第一个用户的 ID。"""
    from backend.config import settings
    db_path = settings.db_path
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM users ORDER BY created_at ASC LIMIT 1").fetchone()
    conn.close()
    return row[0] if row else None


def warmup(user_id: str, target_topic: str | None = None, force: bool = False):
    from backend.indexer import build_topic_index, load_topics, topic_index_exists, _use_qdrant_kb
    from backend.config import settings

    topics = load_topics(user_id)
    if not topics:
        print(f"[错误] 用户 {user_id} 没有 topics.json，请先补充。")
        sys.exit(1)

    # 过滤 topic
    if target_topic:
        if target_topic not in topics:
            print(f"[错误] topic '{target_topic}' 不存在，可用的有: {list(topics.keys())}")
            sys.exit(1)
        topic_keys = [target_topic]
    else:
        topic_keys = list(topics.keys())

    knowledge_base = settings.user_knowledge_path(user_id)
    backend_name = "qdrant" if _use_qdrant_kb() else "local"

    print(f"\n{'='*60}")
    print(f"用户 ID : {user_id}")
    print(f"向量后端  : {backend_name}")
    print(f"知识库目录: {knowledge_base}")
    print(f"待预热主题: {topic_keys}")
    print(f"强制重建  : {force}")
    print(f"{'='*60}\n")

    total = len(topic_keys)
    success = 0
    skipped = 0
    failed = []

    for i, key in enumerate(topic_keys, 1):
        topic_info = topics[key]
        topic_name = topic_info.get("name", key)
        topic_dir = knowledge_base / topic_info.get("dir", key)

        print(f"[{i}/{total}] {topic_name} ({key})")

        # 检查知识库目录是否存在
        if not topic_dir.exists():
            print(f"  ⚠ 知识库目录不存在，跳过: {topic_dir}")
            skipped += 1
            continue

        # 检查文件数量
        md_files = list(topic_dir.glob("**/*.md")) + list(topic_dir.glob("**/*.txt"))
        if not md_files:
            print(f"  ⚠ 知识库目录为空（无 .md/.txt），跳过")
            skipped += 1
            continue

        print(f"  → 共 {len(md_files)} 个文档文件")

        # 已建索引检查走 topic_index_exists（后端无关：qdrant 查 collection，
        # 本地查 persist 目录）——不再直接看 .index_cache 目录，那个判断在
        # Docker/Qdrant 部署下会误判（目录里只剩 manifest 也被当成有索引）。
        already = topic_index_exists(key, user_id)
        if already and not force:
            print(f"  ✓ 索引已存在（{backend_name}），跳过（用 --force 强制重建）")
            success += 1
            continue

        # 构建索引。force_rebuild=True 时构建器自己做 manifest 增量：
        # 只重嵌变更文件；文件没变则秒级返回。
        start = time.time()
        try:
            print(f"  → 开始构建索引，调用 embedding API 中...", flush=True)
            build_topic_index(key, user_id, force_rebuild=force)
            elapsed = time.time() - start
            print(f"  ✓ 完成，耗时 {elapsed:.1f}s")
            success += 1
        except Exception as e:
            elapsed = time.time() - start
            print(f"  ✗ 失败（{elapsed:.1f}s）: {e}")
            failed.append((key, str(e)))

        print()

    # 汇总
    print(f"\n{'='*60}")
    print(f"预热完成")
    print(f"  成功: {success}")
    print(f"  跳过: {skipped}")
    print(f"  失败: {len(failed)}")
    if failed:
        print(f"\n失败的主题:")
        for key, err in failed:
            print(f"  - {key}: {err}")
    print(f"{'='*60}\n")

    if failed:
        print("部分主题失败，请检查 embedding 服务是否正常。")
        print("失败的主题不影响系统使用，只是该主题首次出题时仍需现场建索引。")


def main():
    parser = argparse.ArgumentParser(description="知识库索引预热脚本")
    parser.add_argument("--user-id", default=None, help="指定用户ID（默认自动读取第一个用户）")
    parser.add_argument("--topic", default=None, help="只预热指定 topic key（不传则全部预热）")
    parser.add_argument("--force", action="store_true", help="强制重建已有缓存")
    args = parser.parse_args()

    user_id = args.user_id
    if not user_id:
        user_id = get_first_user_id()
        if not user_id:
            print("[错误] 数据库中没有用户，请先启动服务并登录一次。")
            sys.exit(1)
        print(f"[自动选择] 使用第一个用户: {user_id}")

    warmup(user_id=user_id, target_topic=args.topic, force=args.force)


if __name__ == "__main__":
    main()
