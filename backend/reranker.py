"""Reranker API client — Cross-Encoder re-ranking via external API.

Calls a Cohere-compatible /v1/rerank endpoint (e.g. Gitee AI Qwen3-Reranker).
Supports channel_manager failover and Redis result caching.
Graceful degradation: returns original order on any failure.
"""
import asyncio
import hashlib
import logging
import struct

import httpx

from backend.redis_cache import get_cache

logger = logging.getLogger("uvicorn")

# rerank 是轻量 cross-encoder，正常亚秒级返回；read 不必给到 90s。外层 drill 总预算
# 有限，rerank 卡满会连带把已检索好的 chunk 一起超时丢弃，故 read 收紧（默认 30s）。
# read 在 rerank() 内按 tuning.retrieval.reranker_read_timeout 取值（call-time，
# 设置界面「检索」可调、热生效）；connect/write/pool 保持小值。

# 送去打分的输入上限（仅影响打分入参，不影响回传的完整原文）：
# - MAX_RERANK_DOCS: 候选条数上限。调用方传 top_n=len(chunks)，评测 --top-k 50
#   或大检索集会撑爆上游 rerank API 的文档条数上限；最终调用方还会 [:final_top_n]
#   截到 10，故 50 足够覆盖。
# - MAX_DOC_CHARS: 单条文档字符上限。MarkdownNodeParser 单段可达上万字，整段进
#   payload 会触发上游单文档 token 上限 → 400/413。
# - MAX_QUERY_CHARS: query 字符上限，避免超长 query 同样撑爆输入。
MAX_RERANK_DOCS = 50
MAX_DOC_CHARS = 2000
MAX_QUERY_CHARS = 512


def _get_reranker_config() -> dict | None:
    """Resolve reranker API config from channel_manager or .env fallback.

    Returns {"api_base", "api_key", "api_model", "channel_id", "proxy"} or None.
    ``channel_id`` is the channel_manager id (used to report success/failure so
    a bad channel cools down), or None on the .env fallback path.
    """
    from backend.channel_manager import get_channel, has_channels

    if has_channels("reranker"):
        ch = get_channel("reranker")
        # Require both base and key: an enabled-but-keyless channel would
        # otherwise yield a non-None config and fire a guaranteed-401 request
        # every drill. Fall through to .env when the channel is incomplete.
        if ch and ch.get("api_base") and ch.get("api_key"):
            return {
                "api_base": ch["api_base"],
                "api_key": ch["api_key"],
                "api_model": ch.get("api_model", ""),
                "channel_id": ch.get("id"),
                "proxy": ch.get("proxy", "") or "",
            }

    from backend.ai_config import get_effective
    base = get_effective("reranker", "api_base")
    key = get_effective("reranker", "api_key")
    model = get_effective("reranker", "api_model")
    if base and key:
        return {"api_base": base, "api_key": key, "api_model": model, "channel_id": None, "proxy": ""}
    return None


def _report(channel_id: str | None, ok: bool) -> None:
    """Feed the rerank outcome back to channel_manager so a failing channel
    cools down (3-strike / 60s) and a recovered one resets. No-op on the .env
    fallback path (channel_id is None — no channel state to track)."""
    if not channel_id:
        return
    try:
        from backend.channel_manager import report_error, report_success
        (report_success if ok else report_error)("reranker", channel_id)
    except Exception:
        pass


_RERANK_CACHE_SCHEMA = "rerank-v2"


def _cache_key(
    query: str,
    chunks: list[str],
    top_n: int,
    *,
    model: str = "",
    endpoint: str = "",
) -> str:
    """Length-prefixed hash of provider identity, query, and chunks.

    Prefixing each segment with its byte length prevents boundary aliasing:
    without it, (query="ab", chunks=["c","d"]) and (query="a", chunks=["bc","d"])
    hash identically, so a cache hit would silently reorder the wrong set.
    top_n is part of the key because the cached index-list length depends on it.
    Provider identity prevents a hot model/channel switch from silently reusing
    rankings produced by the previous reranker.
    """
    h = hashlib.sha256()
    h.update(struct.pack("<I", top_n))
    for part in (
        _RERANK_CACHE_SCHEMA,
        model,
        endpoint.rstrip("/"),
        str(MAX_RERANK_DOCS),
        str(MAX_DOC_CHARS),
        str(MAX_QUERY_CHARS),
        query,
        *chunks,
    ):
        pb = part.encode("utf-8")
        h.update(struct.pack("<I", len(pb)))
        h.update(pb)
    return f"rerank:{h.hexdigest()[:24]}"


def _validated_indices(
    value: object,
    *,
    chunk_count: int,
    max_results: int,
) -> list[int] | None:
    """Return a safe reranker index list, or None for malformed data."""
    if not isinstance(value, list) or not value:
        return None
    if len(value) > max_results:
        return None

    indices: list[int] = []
    seen: set[int] = set()
    for index in value:
        if not isinstance(index, int) or isinstance(index, bool):
            return None
        if index < 0 or index >= chunk_count:
            return None
        if index in seen:
            return None
        seen.add(index)
        indices.append(index)
    return indices


async def rerank(
    query: str,
    chunks: list[str],
    top_n: int = 10,
    *,
    read_timeout: float | None = None,
) -> tuple[list[str], str]:
    """Re-rank chunks by relevance to query via Cross-Encoder API.

    Returns (chunks, status) where status is one of:
      "applied"  — reranked successfully (order may have changed)
      "degraded" — a reranker is configured but the call failed → original order
      "off"      — no reranker configured (or nothing to rerank) → original order
    """
    if not chunks or len(chunks) <= 1:
        return chunks, "off"

    config = _get_reranker_config()
    if not config:
        return chunks, "off"

    # 输入上限：仅约束送去打分的候选集与文本长度，避免撑爆上游 rerank API。
    # 注意：候选条数截断后，reordered 仍取 chunks[i] 的【完整原文】（见下方
    # documents 单独做字符截断，chunks 本身不截短），保证回传内容不被截断。
    chunks = chunks[:MAX_RERANK_DOCS]
    query = query[:MAX_QUERY_CHARS]

    effective_top_n = min(top_n, len(chunks))
    api_base = config["api_base"].rstrip("/")
    cache = get_cache()
    ck = _cache_key(
        query,
        chunks,
        effective_top_n,
        model=config.get("api_model", ""),
        endpoint=api_base,
    )
    cached = await asyncio.to_thread(cache.get_json, ck)
    cached_indices = _validated_indices(
        cached,
        chunk_count=len(chunks),
        max_results=effective_top_n,
    )
    if cached_indices is not None:
        logger.debug("Reranker cache hit: %s", ck)
        return [chunks[index] for index in cached_indices], "applied"

    url = api_base if api_base.endswith("/rerank") else f"{api_base}/rerank"
    channel_id = config.get("channel_id")

    payload = {
        "model": config["api_model"],
        "query": query,
        # 单条文档截断仅用于打分输入；reordered 回传时取的是未截断的 chunks[i] 完整原文。
        "documents": [c[:MAX_DOC_CHARS] for c in chunks],
        "top_n": effective_top_n,
        "return_documents": False,
    }

    from backend.ai_config import get_retrieval_setting
    read_to = (
        read_timeout if read_timeout is not None
        else get_retrieval_setting("reranker_read_timeout")
    )

    try:
        client_kw: dict = {
            "timeout": httpx.Timeout(connect=10.0, read=read_to, write=10.0, pool=10.0),
            "headers": {"User-Agent": "curl/7.88.1"},
            "follow_redirects": True,
        }
        proxy = config.get("proxy", "")
        if proxy:
            from backend.llm_provider import _normalize_proxy_url
            client_kw["proxy"] = _normalize_proxy_url(proxy)

        async with httpx.AsyncClient(**client_kw) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        # HTTP 200 + parseable body → the channel is reachable, so reset its
        # failure count even if the model returned nothing useful below (that's
        # an upstream/data issue, not a channel fault).
        _report(channel_id, True)

        results = data.get("results", [])
        if not results:
            logger.warning("Reranker returned empty results")
            return chunks, "degraded"

        ranked_results = sorted(
            results,
            key=lambda r: r.get("relevance_score", 0) if isinstance(r, dict) else 0,
            reverse=True,
        )
        raw_indices = [
            r.get("index") if isinstance(r, dict) else None
            for r in ranked_results
        ]
        indices = _validated_indices(
            raw_indices,
            chunk_count=len(chunks),
            max_results=effective_top_n,
        )
        if indices is None:
            logger.warning("Reranker returned invalid indices")
            return chunks, "degraded"

        reordered = [chunks[index] for index in indices]

        await asyncio.to_thread(cache.set_json, ck, indices, 3600)
        logger.info(
            "Reranker applied: query=%r, %d chunks → top %d, model=%s",
            query[:50], len(chunks), len(reordered), config["api_model"],
        )
        return reordered, "applied"

    except httpx.TimeoutException:
        logger.warning("Reranker timeout (%ds read): query=%r, %d chunks", read_to, query[:50], len(chunks))
        _report(channel_id, False)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        logger.warning("Reranker HTTP error %d: %s", status, e.response.text[:200])
        # 400/413/422 是确定性的输入过大/格式错误，换渠道也救不了，不应污染
        # 渠道失败计数（否则 3 次后会误把可用渠道冷却 60s）；其余（5xx 等）仍上报。
        if status not in (400, 413, 422):
            _report(channel_id, False)
    except Exception as e:
        logger.warning("Reranker failed: %s", e)
        _report(channel_id, False)

    return chunks, "degraded"
