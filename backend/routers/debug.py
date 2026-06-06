"""临时内存诊断端点（需登录，只读）。

正常运行零开销。想看 Python 层分配明细时，在启动 backend 前设环境变量
PYTHONTRACEMALLOC=10 并重启，/api/debug/memory 的 tracemalloc 段就会按源码
位置列出分配大头（含 import 期）。诊断完把该端点与环境变量一并移除即可。

app_caches 段对内部模块全局做 best-effort 读取，故包一层 try/except —— 这是
诊断端点的合理例外（绝不能因内部结构变动而让端点本身 500）。
"""
import gc
import sys
import tracemalloc
from collections import Counter

from fastapi import APIRouter, Depends

from backend.auth import get_current_user

router = APIRouter(prefix="/api")


def _process_mem() -> dict:
    """从 /proc 读常驻 / 虚拟内存（MB）；非 Linux（如本机调试）返回空。"""
    out: dict[str, float] = {}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith(("VmRSS:", "VmSize:", "VmData:")):
                    parts = line.split()
                    out[parts[0].rstrip(":")] = round(int(parts[1]) / 1024, 1)  # kB -> MB
    except OSError:
        pass
    return out


def _app_caches() -> dict:
    """应用自有缓存的条目数 —— 这些才是你能直接调的旋钮。"""
    caches: dict = {}
    try:
        from backend import indexer
        with indexer._index_cache_lock:
            caches["llama_index_cache"] = {
                "entries": len(indexer._index_cache),
                "max": indexer._INDEX_CACHE_MAX_SIZE,
            }
    except Exception as e:  # noqa: BLE001 — 诊断端点必须永不崩
        caches["llama_index_cache"] = f"n/a ({e})"
    try:
        from backend import live_store
        caches["live_sessions"] = {
            name: len(getattr(live_store, name)._data)
            for name in ("graphs", "drill_sessions", "job_prep_sessions",
                         "algorithm_sessions", "rag_eval_jobs")
        }
    except Exception as e:  # noqa: BLE001
        caches["live_sessions"] = f"n/a ({e})"
    return caches


def _gc_summary(top: int = 12) -> dict:
    objs = gc.get_objects()
    counts = Counter(type(o).__name__ for o in objs)
    return {
        "tracked_objects": len(objs),
        "gc_counts": list(gc.get_count()),
        "top_types_by_count": counts.most_common(top),
    }


def _tracemalloc_top(top: int = 15) -> dict:
    if not tracemalloc.is_tracing():
        return {
            "tracing": False,
            "enable_hint": "启动 backend 前设 PYTHONTRACEMALLOC=10 并重启，本段会列出 Python 分配大头（含 import 期）。",
        }
    cur, peak = tracemalloc.get_traced_memory()
    stats = tracemalloc.take_snapshot().statistics("lineno")[:top]
    return {
        "tracing": True,
        "traced_current_mb": round(cur / 1048576, 1),
        "traced_peak_mb": round(peak / 1048576, 1),
        "top": [
            {"where": str(s.traceback), "size_mb": round(s.size / 1048576, 2), "blocks": s.count}
            for s in stats
        ],
    }


@router.get("/debug/memory")
def debug_memory(_user_id: str = Depends(get_current_user)):
    """内存诊断快照（只读）。gc 段会遍历全部对象，可能短暂耗时 ~1s。"""
    return {
        "process_mb": _process_mem(),
        "modules_loaded": len(sys.modules),
        "app_caches": _app_caches(),
        "python_objects": _gc_summary(),
        "tracemalloc": _tracemalloc_top(),
    }
