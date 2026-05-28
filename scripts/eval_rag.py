#!/usr/bin/env python3
"""RAG 离线召回率评估

用途：用一组手工标注的 query 评估 RAG 召回质量，量化"召回率到底是多少"，
     让所有 RAG 改动（chunk 策略 / RRF 参数 / top_k / HyDE 等）都可量化对比。

数据集：data/eval/rag_queries.json
判断逻辑：chunk 文本中包含 must_include_any 中任一关键词即视为命中（不区分大小写）

使用：
    # 跑全部 query
    python -m scripts.eval_rag

    # 只评 agent 域
    python -m scripts.eval_rag --filter-topic agent

    # 显示每 query 召回的 chunk 文本预览
    python -m scripts.eval_rag --verbose

    # Docker 容器内
    docker compose exec backend python /app/scripts/eval_rag.py
"""

import sys
import json
import asyncio
import argparse
import sqlite3
from pathlib import Path

# 兼容本地（python -m scripts.eval_rag）和 Docker（python /app/scripts/eval_rag.py）
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def get_first_user_id() -> str | None:
    """读 SQLite 第一个用户 id（同 warmup_index.py 风格）。"""
    from backend.config import settings
    db_path = settings.db_path
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM users ORDER BY created_at ASC LIMIT 1").fetchone()
    conn.close()
    return row[0] if row else None


def keyword_hit(chunk: str, must_include_any: list[str]) -> bool:
    """命中规则：chunk 包含任一关键词（不区分大小写）。"""
    low = chunk.lower()
    return any(kw.lower() in low for kw in must_include_any)


def first_hit_rank(chunks: list[str], must_include_any: list[str]) -> int | None:
    """返回首个命中 chunk 的 1-based 排名；无命中返回 None。"""
    for i, c in enumerate(chunks, start=1):
        if keyword_hit(c, must_include_any):
            return i
    return None


async def eval_one(query_def: dict, user_id: str, top_k: int = 10) -> dict:
    """对单 query 跑 retrieve_for_drill 并计算指标。"""
    from backend.graphs.rag_retrieval import retrieve_for_drill

    chunks, stats = await retrieve_for_drill(
        topic=query_def["topic"],
        user_id=user_id,
        weak_points=[query_def["query"]],
        fallback_query=query_def["query"],
        per_query_top_k=top_k,
        final_top_n=top_k,
    )
    must_include = (
        query_def.get("must_include_any")
        or query_def.get("expected_keywords", [])
    )
    rank = first_hit_rank(chunks, must_include)
    return {
        "id": query_def["id"],
        "topic": query_def["topic"],
        "query": query_def["query"],
        "n_chunks": len(chunks),
        "first_hit_rank": rank,
        "hit_at_5": rank is not None and rank <= 5,
        "hit_at_10": rank is not None and rank <= 10,
        "chunks_preview": [c[:180] for c in chunks[:3]],
    }


async def run_eval(user_id: str, filter_topic: str | None, verbose: bool, top_k: int):
    eval_path = REPO_ROOT / "data" / "eval" / "rag_queries.json"
    if not eval_path.exists():
        print(f"[错误] 评估集不存在：{eval_path}")
        sys.exit(1)

    data = json.loads(eval_path.read_text(encoding="utf-8"))
    queries = data.get("queries", [])
    if filter_topic:
        queries = [q for q in queries if q["topic"] == filter_topic]
    if not queries:
        print(f"[错误] filter-topic={filter_topic} 没有匹配的 query")
        sys.exit(1)

    print(f"\n{'='*72}")
    print(f"用户 ID    : {user_id}")
    print(f"评估集     : {eval_path}")
    print(f"待跑 query : {len(queries)} (filter={filter_topic or 'all'}, top_k={top_k})")
    print(f"{'='*72}\n")

    results: list[dict] = []
    for i, qd in enumerate(queries, 1):
        print(f"[{i}/{len(queries)}] {qd['id']:10s} | {qd['topic']:6s} | {qd['query']}", flush=True)
        try:
            r = await eval_one(qd, user_id, top_k=top_k)
            results.append(r)
            mark = "✓" if r["hit_at_5"] else ("△" if r["hit_at_10"] else "✗")
            rank_str = f"#{r['first_hit_rank']}" if r["first_hit_rank"] else "—"
            print(f"            {mark} 召回 {r['n_chunks']}/{top_k}, 首命中 {rank_str}")
            if verbose:
                for j, c in enumerate(r["chunks_preview"], 1):
                    print(f"               [{j}] {c}")
        except Exception as exc:
            print(f"            ✗ 失败: {exc}")
            results.append({
                "id": qd["id"], "topic": qd["topic"], "error": str(exc),
                "hit_at_5": False, "hit_at_10": False, "first_hit_rank": None,
            })
        print()

    # ── 汇总 ──
    total = len(results)
    hit5 = sum(1 for r in results if r.get("hit_at_5"))
    hit10 = sum(1 for r in results if r.get("hit_at_10"))
    mrr_sum = sum(1.0 / r["first_hit_rank"] for r in results if r.get("first_hit_rank"))
    mrr = mrr_sum / total if total else 0.0

    print(f"\n{'='*72}")
    print(f"汇总")
    print(f"  recall@5  : {hit5}/{total} = {hit5/total*100:.1f}%")
    print(f"  recall@10 : {hit10}/{total} = {hit10/total*100:.1f}%")
    print(f"  MRR       : {mrr:.3f}")
    print(f"{'='*72}")

    topics = sorted({r["topic"] for r in results})
    if len(topics) > 1:
        print(f"\n按 topic 分组：")
        for t in topics:
            ts = [r for r in results if r["topic"] == t]
            n = len(ts)
            th5 = sum(1 for r in ts if r.get("hit_at_5"))
            th10 = sum(1 for r in ts if r.get("hit_at_10"))
            print(f"  {t:8s}: recall@5={th5}/{n}={th5/n*100:5.1f}%  |  recall@10={th10}/{n}={th10/n*100:5.1f}%")
        print()

    # ── 退出码：所有 query 都 hit_at_10 视为通过（便于 CI 集成）──
    return 0 if hit10 == total else 1


def main():
    p = argparse.ArgumentParser(description="RAG 召回率离线评估")
    p.add_argument("--user-id", default=None, help="指定用户 ID（默认读第一个）")
    p.add_argument("--filter-topic", default=None, help="只评指定 topic")
    p.add_argument("--top-k", type=int, default=10, help="检索 top_k（默认 10）")
    p.add_argument("--verbose", action="store_true", help="显示每 query 召回 chunk 预览")
    args = p.parse_args()

    user_id = args.user_id
    if not user_id:
        user_id = get_first_user_id()
        if not user_id:
            print("[错误] 数据库中没有用户，请先启动服务登录一次。")
            sys.exit(1)
        print(f"[自动选择] 用户: {user_id}")

    exit_code = asyncio.run(run_eval(user_id, args.filter_topic, args.verbose, args.top_k))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
