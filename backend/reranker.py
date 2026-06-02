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

# Cross-Encoder rerank is typically sub-second to a few seconds. Keep the read
# budget tight (20s, matching the embedding client) so a stalled reranker — it
# sits on the question-generation path — degrades to original order fast instead
# of blocking a drill for 90s. Reranking is optional; never let it dominate.
_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)


def _get_reranker_config() -> dict | None:
    """Resolve reranker API config from channel_manager or .env fallback.

    Returns {"api_base", "api_key", "api_model", "channel_id"} or None.
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
            }

    from backend.ai_config import get_effective
    base = get_effective("reranker", "api_base")
    key = get_effective("reranker", "api_key")
    model = get_effective("reranker", "api_model")
    if base and key:
        return {"api_base": base, "api_key": key, "api_model": model, "channel_id": None}
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


def _cache_key(query: str, chunks: list[str], top_n: int) -> str:
    """Length-prefixed hash of (top_n, query, chunks).

    Prefixing each segment with its byte length prevents boundary aliasing:
    without it, (query="ab", chunks=["c","d"]) and (query="a", chunks=["bc","d"])
    hash identically, so a cache hit would silently reorder the wrong set.
    top_n is part of the key because the cached index-list length depends on it.
    """
    h = hashlib.sha256()
    h.update(struct.pack("<I", top_n))
    for part in (query, *chunks):
        pb = part.encode("utf-8")
        h.update(struct.pack("<I", len(pb)))
        h.update(pb)
    return f"rerank:{h.hexdigest()[:24]}"


async def rerank(query: str, chunks: list[str], top_n: int = 10) -> tuple[list[str], bool]:
    """Re-rank chunks by relevance to query via Cross-Encoder API.

    Returns (reranked_chunks, success). On failure returns (original_chunks, False).
    """
    if not chunks or len(chunks) <= 1:
        return chunks, False

    config = _get_reranker_config()
    if not config:
        return chunks, False

    effective_top_n = min(top_n, len(chunks))
    cache = get_cache()
    ck = _cache_key(query, chunks, effective_top_n)
    cached = await asyncio.to_thread(cache.get_json, ck)
    if cached is not None:
        try:
            reordered = [chunks[i] for i in cached if i < len(chunks)]
            if reordered:
                logger.debug("Reranker cache hit: %s", ck)
                return reordered, True
        except (TypeError, IndexError):
            pass

    api_base = config["api_base"].rstrip("/")
    url = api_base if api_base.endswith("/rerank") else f"{api_base}/rerank"
    channel_id = config.get("channel_id")

    payload = {
        "model": config["api_model"],
        "query": query,
        "documents": chunks,
        "top_n": effective_top_n,
        "return_documents": False,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
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
            return chunks, False

        indices = [r["index"] for r in sorted(results, key=lambda r: r.get("relevance_score", 0), reverse=True)]
        reordered = [chunks[i] for i in indices if i < len(chunks)]

        await asyncio.to_thread(cache.set_json, ck, indices, 3600)
        logger.info(
            "Reranker applied: query=%r, %d chunks → top %d, model=%s",
            query[:50], len(chunks), len(reordered), config["api_model"],
        )
        return reordered, True

    except httpx.TimeoutException:
        logger.warning("Reranker timeout (90s): query=%r, %d chunks", query[:50], len(chunks))
        _report(channel_id, False)
    except httpx.HTTPStatusError as e:
        logger.warning("Reranker HTTP error %d: %s", e.response.status_code, e.response.text[:200])
        _report(channel_id, False)
    except Exception as e:
        logger.warning("Reranker failed: %s", e)
        _report(channel_id, False)

    return chunks, False
