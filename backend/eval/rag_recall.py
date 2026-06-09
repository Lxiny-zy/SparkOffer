"""RAG retrieval-quality eval — measures whether knowledge retrieval actually
surfaces relevant chunks.

This is the missing counterpart to ``backend/eval/run.py``: that harness scores
the *generated questions* (coverage / difficulty / diversity / llm_judge); this
one scores the *retrieval step itself*, which until now had no metric at all.

Consumes ``data/eval/rag_queries.json``. For each query we run the real
retrieval path and score every returned chunk by keyword matching — no per-chunk
ground-truth labeling required (you extend the set by adding ``must_include_any``
/ ``expected_keywords``, same as the existing file documents).

Metrics (k = ``--top-k``):
  - HitRate@k    : fraction of queries with >=1 chunk hitting ``must_include_any``.
                   The closest proxy to "did retrieval surface anything usable".
  - MRR@k        : mean reciprocal rank of the FIRST hit chunk. Sensitive to
                   ranking quality — this is the number that should move when a
                   reranker (P1-F) is added.
  - KwCoverage@k : fraction of ``expected_keywords`` found across the union of
                   top-k chunks. Proxy for recall completeness.
  - Precision@k  : hit chunks / returned chunks. Relevance density — exposes
                   "retrieved a lot but mostly irrelevant".

NOTE on rigor: these are keyword-proxy metrics, not strict IR recall (which would
need exhaustive per-chunk relevance labels). They are designed for *before/after*
comparison across retrieval changes (chunking, hybrid, rerank), which is exactly
what the optimization plan needs. Absolute values are less meaningful than deltas.

Usage
-----
    python -m backend.eval.rag_recall                      # all queries, k=5, dense path
    python -m backend.eval.rag_recall --top-k 10
    python -m backend.eval.rag_recall --topic python       # one topic only
    python -m backend.eval.rag_recall --via drill          # exercise the RRF fusion path
    python -m backend.eval.rag_recall --user-id 0c6f9fc1   # pin the retrieval user
    python -m backend.eval.rag_recall --output-csv backend/eval/reports/rag_baseline.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
import time
from pathlib import Path

from backend.config import settings
from backend.indexer import load_topics, safe_retrieve_topic_context

logger = logging.getLogger("uvicorn")

QUERIES_PATH = settings.base_dir / "data" / "eval" / "rag_queries.json"
REPORTS_DIR = Path(__file__).parent / "reports"


# ── User discovery ──────────────────────────────────────────────────────────

def _discover_user() -> str | None:
    """Find a user whose knowledge dir actually contains indexed docs.

    Retrieval is scoped per-user (``user_knowledge_path`` = data/users/{id}/knowledge),
    so the eval needs a user with a populated knowledge base. We pick the first
    user dir that has at least one .md under knowledge/ and a topics.json.
    Override with --user-id when you want a specific one.
    """
    users_root = settings.base_dir / "data" / "users"
    if not users_root.exists():
        return None
    for user_dir in sorted(users_root.iterdir()):
        if not user_dir.is_dir():
            continue
        uid = user_dir.name
        knowledge = settings.user_knowledge_path(uid)
        topics = settings.user_topics_path(uid)
        if topics.exists() and knowledge.exists() and any(knowledge.rglob("*.md")):
            return uid
    return None


# ── Scoring ──────────────────────────────────────────────────────────────────

def _chunk_hits(chunk: str, keywords: list[str]) -> bool:
    """A chunk 'hits' if it contains any of ``keywords`` (case-insensitive substring).

    Substring match works for both CJK ('多头') and latin ('multi-head') terms.
    """
    low = chunk.lower()
    return any(kw.lower() in low for kw in keywords if kw)


def _score_query(chunks: list[str], q: dict, k: int) -> dict:
    """Compute per-query metrics from the retrieved chunk list (already top-k)."""
    must_any = q.get("must_include_any", [])
    expected = q.get("expected_keywords", [])

    hit_flags = [_chunk_hits(c, must_any) for c in chunks]
    n_hits = sum(hit_flags)

    # HitRate: at least one hit chunk.
    hit_rate = 1.0 if n_hits > 0 else 0.0

    # MRR: reciprocal rank of the first hit chunk (rank starts at 1).
    mrr = 0.0
    for rank, flag in enumerate(hit_flags, start=1):
        if flag:
            mrr = 1.0 / rank
            break

    # KwCoverage: how many expected_keywords appear anywhere in the top-k union.
    if expected:
        union_low = "\n".join(chunks).lower()
        covered = sum(1 for kw in expected if kw and kw.lower() in union_low)
        kw_cov = covered / len(expected)
    else:
        kw_cov = 0.0

    # Precision: hit chunks / returned chunks (guard empty).
    precision = (n_hits / len(chunks)) if chunks else 0.0

    return {
        "hit_rate": hit_rate,
        "mrr": mrr,
        "kw_cov": kw_cov,
        "precision": precision,
        "n_chunks": len(chunks),
        "n_hits": n_hits,
    }


# ── Retrieval ──────────────────────────────────────────────────────────────────

async def _retrieve(topic: str, query: str, user_id: str, k: int, via: str) -> list[str]:
    """Run one query through the chosen retrieval path; return chunk texts."""
    if via == "drill":
        # Exercise the RRF fusion path. Treating the single eval query as the
        # sole weak_point means a fallback query is appended (see retrieve_for_drill),
        # so this measures the fused pipeline, not a clean single-query recall.
        from backend.graphs.rag_retrieval import retrieve_for_drill
        chunks, _stats = await retrieve_for_drill(
            topic=topic, user_id=user_id, weak_points=[query],
            fallback_query=f"{topic} 核心知识点", per_query_top_k=k, final_top_n=k,
        )
        return chunks
    # Default: dense single-query retrieval — the atomic capability all paths build on.
    return await safe_retrieve_topic_context(topic, query, user_id, top_k=k)


# ── Aggregation / reporting ──────────────────────────────────────────────────

_METRIC_KEYS = ["hit_rate", "mrr", "kw_cov", "precision"]


def _mean(rows: list[dict], key: str) -> float:
    return sum(r[key] for r in rows) / len(rows) if rows else 0.0


def _print_group(label: str, rows: list[dict]):
    if not rows:
        return
    print(
        f"  {label:<22} "
        f"HitRate={_mean(rows, 'hit_rate'):.3f}  "
        f"MRR={_mean(rows, 'mrr'):.3f}  "
        f"KwCov={_mean(rows, 'kw_cov'):.3f}  "
        f"Prec={_mean(rows, 'precision'):.3f}  "
        f"(n={len(rows)})"
    )


async def _main_async(args: argparse.Namespace) -> int:
    if not QUERIES_PATH.exists():
        print(f"ERROR: eval set not found at {QUERIES_PATH}", file=sys.stderr)
        return 2
    spec = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    queries: list[dict] = spec.get("queries", [])

    if args.topic:
        queries = [q for q in queries if q.get("topic") == args.topic]
    if not queries:
        print(f"ERROR: no queries matched topic={args.topic!r}", file=sys.stderr)
        return 2

    user_id = args.user_id or _discover_user()
    if not user_id:
        print(
            "ERROR: no user with a populated knowledge base found under data/users/.\n"
            "       Configure a user's knowledge dir first, or pass --user-id.",
            file=sys.stderr,
        )
        return 2

    topic_map = load_topics(user_id)
    print(
        f"RAG recall eval · user={user_id} · via={args.via} · k={args.top_k} · "
        f"{len(queries)} queries · topics={list(topic_map.keys())}"
    )
    print("-" * 78)

    results: list[dict] = []
    for q in queries:
        topic = q.get("topic", "")
        if topic not in topic_map:
            logger.warning("Skipping %s — topic %r not in user's topics.json", q.get("id"), topic)
            continue
        t0 = time.perf_counter()
        try:
            chunks = await _retrieve(topic, q["query"], user_id, args.top_k, args.via)
        except Exception as e:
            logger.exception("Retrieval failed for %s: %s", q.get("id"), e)
            chunks = []
        ms = (time.perf_counter() - t0) * 1000.0

        m = _score_query(chunks, q, args.top_k)
        row = {
            "id": q.get("id", ""),
            "topic": topic,
            "difficulty": q.get("difficulty", "unspecified"),
            "type": q.get("type", "unspecified"),
            **m,
            "ms": round(ms, 0),
        }
        results.append(row)
        mark = "✓" if m["hit_rate"] else "✗"
        print(
            f"  {mark} {row['id']:<12} {topic:<7} "
            f"hit={m['hit_rate']:.0f} mrr={m['mrr']:.2f} kwcov={m['kw_cov']:.2f} "
            f"prec={m['precision']:.2f} ({m['n_hits']}/{m['n_chunks']} chunks, {ms:.0f}ms)"
        )

    if not results:
        print("ERROR: no queries were evaluated (topic mismatch?).", file=sys.stderr)
        return 2

    print("-" * 78)
    print("Overall:")
    _print_group("ALL", results)

    # Headline split: hard should score below easy — if it doesn't, the set still
    # lacks discriminating power (the whole reason v2 added hard queries).
    by_diff: dict[str, list[dict]] = {}
    for r in results:
        by_diff.setdefault(r["difficulty"], []).append(r)
    if len(by_diff) > 1:
        print("By difficulty:")
        for d, rows in sorted(by_diff.items()):
            _print_group(d, rows)

    by_topic: dict[str, list[dict]] = {}
    for r in results:
        by_topic.setdefault(r["topic"], []).append(r)
    if len(by_topic) > 1:
        print("By topic:")
        for t, rows in sorted(by_topic.items()):
            _print_group(t, rows)

    by_type: dict[str, list[dict]] = {}
    for r in results:
        by_type.setdefault(r["type"], []).append(r)
    if len(by_type) > 1 or "unspecified" not in by_type:
        print("By type:")
        for t, rows in sorted(by_type.items()):
            _print_group(t, rows)

    # Flag the queries that fully missed — these are the actionable failures.
    misses = [r["id"] for r in results if r["hit_rate"] == 0.0]
    if misses:
        print(f"\nFull misses ({len(misses)}): {', '.join(misses)}")

    out_path = Path(args.output_csv) if args.output_csv else REPORTS_DIR / f"rag_recall_{int(time.time())}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["id", "topic", "difficulty", "type", "hit_rate", "mrr",
                           "kw_cov", "precision", "n_chunks", "n_hits", "ms"],
        )
        writer.writeheader()
        writer.writerows(results)
    print(f"\nWrote {len(results)} rows to {out_path}")
    return 0


def main():
    p = argparse.ArgumentParser(
        prog="python -m backend.eval.rag_recall",
        description="Measure RAG retrieval quality (HitRate / MRR / KwCoverage / Precision).",
    )
    p.add_argument("--top-k", type=int, default=5, help="Chunks to retrieve per query (default: 5)")
    p.add_argument("--topic", default="", help="Restrict to one topic (python/java/agent)")
    p.add_argument("--via", choices=["topic_context", "drill"], default="topic_context",
                   help="Retrieval path: 'topic_context' = dense single-query (default), "
                        "'drill' = RRF fusion pipeline")
    p.add_argument("--user-id", default="", help="Retrieval user (default: auto-discover)")
    p.add_argument("--output-csv", default="", help="CSV path (default: reports/rag_recall_<ts>.csv)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    # Bootstrap the same subsystems main.py starts — retrieval needs the embedding
    # channel pool, and index build touches DB-less LlamaIndex settings.
    try:
        from backend.ai_config import init_config
        init_config()
    except Exception as e:
        logger.warning("ai_config.init_config failed (%s) — relying on .env", e)
    # Set LlamaSettings.embed_model exactly like main.py's lifespan. Without this,
    # a disk-cache-hit retrieval falls back to LlamaIndex's default OpenAI ada-002
    # against the public OpenAI endpoint — every query then times out (the cause
    # of the all-zero baseline report).
    try:
        from backend.indexer import _init_llama_settings
        _init_llama_settings()
    except Exception as e:
        logger.warning("_init_llama_settings failed: %s", e)
    try:
        from backend.storage.database import init_all_tables
        init_all_tables()
    except Exception as e:
        logger.warning("init_all_tables failed: %s", e)

    try:
        exit_code = asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        print("\nAborted", file=sys.stderr)
        exit_code = 130
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
