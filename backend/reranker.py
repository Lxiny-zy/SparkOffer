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


def _get_reranker_config(exclude: set[str] | None = None) -> dict | None:
    """Resolve reranker API config from channel_manager or .env fallback.

    Returns provider config including ``channel_id`` and an internal probe token,
    or None.  ``exclude`` contains managed channels already tried by this call.
    ``channel_id`` is the channel_manager id (used to report success/failure so
    a bad channel cools down), or None on the .env fallback path.
    """
    from backend.channel_manager import (
        get_channel,
        get_next_channel,
        has_channels,
        release_probe,
    )

    if has_channels("reranker"):
        skipped = set(exclude or ())
        while True:
            ch = (
                get_next_channel("reranker", skipped)
                if skipped
                else get_channel("reranker")
            )
            if not ch:
                break

            channel_id = ch.get("id")
            probe_token = ch.get("_probe_token")
            # Runtime validation normally prevents incomplete enabled channels,
            # but legacy JSON may still contain one. Skip it without consuming a
            # HALF_OPEN lease or firing a guaranteed-401 request.
            if ch.get("api_base") and ch.get("api_key"):
                return {
                    "api_base": ch["api_base"],
                    "api_key": ch["api_key"],
                    "api_model": ch.get("api_model", ""),
                    "channel_id": channel_id,
                    "probe_token": probe_token,
                    "proxy": ch.get("proxy", "") or "",
                }
            if channel_id:
                if probe_token is not None:
                    release_probe("reranker", channel_id, probe_token)
                skipped.add(channel_id)

    from backend.ai_config import get_effective
    base = get_effective("reranker", "api_base")
    key = get_effective("reranker", "api_key")
    model = get_effective("reranker", "api_model")
    if base and key:
        return {
            "api_base": base,
            "api_key": key,
            "api_model": model,
            "channel_id": None,
            "probe_token": None,
            "proxy": "",
        }
    return None


def _report(
    channel_id: str | None,
    ok: bool,
    probe_token: str | None = None,
) -> None:
    """Feed the rerank outcome back to channel_manager so a failing channel
    cools down (3-strike / 60s) and a recovered one resets. No-op on the .env
    fallback path (channel_id is None — no channel state to track)."""
    if not channel_id:
        return
    try:
        from backend.channel_manager import report_error, report_success
        reporter = report_success if ok else report_error
        if probe_token is None:
            reporter("reranker", channel_id)
        else:
            reporter("reranker", channel_id, probe_token)
    except Exception:
        pass


def _release_probe(channel_id: str | None, probe_token: str | None) -> None:
    """Return an unused HALF_OPEN admission without declaring recovery."""
    if not channel_id or probe_token is None:
        return
    try:
        from backend.channel_manager import release_probe
        release_probe("reranker", channel_id, probe_token)
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


def _redact_secret(value: object, secret: str) -> str:
    """Keep provider credentials out of diagnostic logs."""
    text = str(value)
    return text.replace(secret, "[redacted]") if secret else text


async def rerank(
    query: str,
    chunks: list[str],
    top_n: int = 10,
    *,
    read_timeout: float | None = None,
) -> tuple[list[str], str]:
    """Re-rank chunks, failing over through every available managed channel."""
    if not chunks or len(chunks) <= 1:
        return chunks, "off"

    config = _get_reranker_config()
    if not config:
        return chunks, "off"

    # Only provider input is bounded. Returned chunks retain their full text.
    chunks = chunks[:MAX_RERANK_DOCS]
    query = query[:MAX_QUERY_CHARS]
    effective_top_n = min(top_n, len(chunks))
    cache = get_cache()

    from backend.ai_config import get_retrieval_setting
    read_to = (
        read_timeout if read_timeout is not None
        else get_retrieval_setting("reranker_read_timeout")
    )

    tried: set[str] = set()
    fallback_attempted = False
    while config:
        channel_id = config.get("channel_id")
        probe_token = config.get("probe_token")
        if channel_id:
            tried.add(channel_id)
        elif fallback_attempted:
            # The legacy .env provider has no channel id for ``tried``.
            break
        else:
            fallback_attempted = True

        api_base = config["api_base"].rstrip("/")
        ck = _cache_key(
            query,
            chunks,
            effective_top_n,
            model=config.get("api_model", ""),
            endpoint=api_base,
        )
        try:
            cached = await asyncio.to_thread(cache.get_json, ck)
        except Exception as exc:
            logger.warning("Reranker cache read failed: %s", exc)
            cached = None
        cached_indices = _validated_indices(
            cached,
            chunk_count=len(chunks),
            max_results=effective_top_n,
        )
        if cached_indices is not None:
            logger.debug("Reranker cache hit: %s", ck)
            _release_probe(channel_id, probe_token)
            return [chunks[index] for index in cached_indices], "applied"

        url = api_base if api_base.endswith("/rerank") else f"{api_base}/rerank"
        payload = {
            "model": config["api_model"],
            "query": query,
            "documents": [chunk[:MAX_DOC_CHARS] for chunk in chunks],
            "top_n": effective_top_n,
            "return_documents": False,
        }
        api_key = config["api_key"]

        try:
            client_kw: dict = {
                "timeout": httpx.Timeout(
                    connect=10.0, read=read_to, write=10.0, pool=10.0,
                ),
                "headers": {"User-Agent": "curl/7.88.1"},
                "follow_redirects": True,
            }
            proxy = config.get("proxy", "")
            if proxy:
                from backend.llm_provider import _normalize_proxy_url
                client_kw["proxy"] = _normalize_proxy_url(proxy)

            async with httpx.AsyncClient(**client_kw) as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            logger.warning(
                "Reranker timeout (%ss read): query=%r, %d chunks",
                read_to, query[:50], len(chunks),
            )
            _report(channel_id, False, probe_token)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            detail = _redact_secret(exc.response.text[:200], api_key)
            logger.warning("Reranker HTTP error %d: %s", status, detail)
            if status in (400, 413, 422):
                # These describe deterministic input/shape errors. Switching
                # providers cannot help, and they must not poison health state.
                _release_probe(channel_id, probe_token)
                return chunks, "degraded"
            _report(channel_id, False, probe_token)
        except Exception as exc:
            logger.warning(
                "Reranker failed: %s", _redact_secret(exc, api_key),
            )
            _report(channel_id, False, probe_token)
        else:
            # A parseable 2xx proves reachability. Empty or malformed rankings
            # are data-quality failures and should not cool down the channel.
            _report(channel_id, True, probe_token)
            results = data.get("results", []) if isinstance(data, dict) else []
            if not isinstance(results, list) or not results:
                logger.warning("Reranker returned empty results")
                return chunks, "degraded"

            def _score(item: object) -> float:
                value = item.get("relevance_score") if isinstance(item, dict) else 0
                return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

            ranked_results = sorted(
                results,
                key=_score,
                reverse=True,
            )
            raw_indices = [
                item.get("index") if isinstance(item, dict) else None
                for item in ranked_results
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
            try:
                await asyncio.to_thread(cache.set_json, ck, indices, 3600)
            except Exception as exc:
                logger.warning("Reranker cache write failed: %s", exc)
            logger.info(
                "Reranker applied: query=%r, %d chunks -> top %d, model=%s",
                query[:50], len(chunks), len(reordered), config["api_model"],
            )
            return reordered, "applied"

        config = _get_reranker_config(tried)

    return chunks, "degraded"
