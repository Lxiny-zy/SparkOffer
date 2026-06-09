"""LLM / Embedding provider with multi-channel failover support."""
import asyncio
import logging
import time
from urllib.parse import urlparse, urlunparse, quote

import httpx
from langchain_openai import ChatOpenAI
from llama_index.llms.openai_like import OpenAILike

from backend.ai_config import get_effective, get_config_version

logger = logging.getLogger("uvicorn")

_embedding_instance = None
_llama_llm_instance = None
_llama_config_version = -1
_embedding_config_version = -1

_CUSTOM_HEADERS = {"User-Agent": "curl/7.88.1"}
# read=360s is intentionally ABOVE nginx's proxy_read_timeout (300s) so a long reasoning
# phase is bounded by ONE layer (nginx), not raced between httpx and nginx. The app-level
# 30s idle ping refreshes nginx's timer; it does NOT refresh httpx's read timer (that
# tracks the upstream socket), hence the generous read budget here.
_LLM_TIMEOUT = httpx.Timeout(connect=15.0, read=360.0, write=30.0, pool=30.0)
_EMBED_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)  # 与 vector_memory._EMBED_TIMEOUT_SECONDS 对齐

# Same-channel retry: a transient 5xx/429/timeout is often gone on a quick retry, and
# retrying the same channel first avoids needlessly tripping every channel's cooldown (the
# old code failed over on the first blip → on a single-channel deploy that meant instant
# RuntimeError). Mirrors the embedding path's explicit retry policy.
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_MAX_SAME_CHANNEL_ATTEMPTS = 2          # 1 original + 1 retry
_SAME_CHANNEL_BACKOFF_SECONDS = 1.5

# 上游可接受的 reasoning_effort 档位。当某 provider 新增档位（如 OpenAI gpt-5.x 的
# "xhigh"）时在此集合补一项即可；集合外的取值会被 _resolve_reasoning_effort 丢成 None。
_REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}


def _resolve_reasoning_effort(value) -> str | None:
    """Normalize reasoning_effort. Returns one of {minimal,low,medium,high} or None to skip."""
    if not value:
        return None
    v = str(value).strip().lower()
    if v in ("", "none", "off"):
        return None
    return v if v in _REASONING_EFFORTS else None


def _normalize_proxy_url(proxy: str) -> str:
    """Ensure proxy credentials are properly URL-encoded."""
    if not proxy or "@" not in proxy:
        return proxy
    parsed = urlparse(proxy)
    if not parsed.username:
        return proxy
    username = quote(parsed.username, safe="")
    password = quote(parsed.password or "", safe="")
    host_port = parsed.hostname or ""
    if parsed.port:
        host_port += f":{parsed.port}"
    new_netloc = f"{username}:{password}@{host_port}"
    return urlunparse(parsed._replace(netloc=new_netloc))


_http_client_cache: dict[tuple, tuple[httpx.Client, httpx.AsyncClient]] = {}


def _build_http_clients(proxy: str = "", *, timeout: httpx.Timeout | None = _LLM_TIMEOUT):
    """Return a cached (sync_client, async_client) pair keyed by (proxy, timeout).

    httpx clients are designed to be long-lived and connection-pooled. Building a
    fresh pair on every invoke/ainvoke/astream call (the previous behaviour) and
    never closing them leaked sockets/file descriptors under sustained load.
    """
    timeout_key = None if timeout is None else (timeout.connect, timeout.read, timeout.write, timeout.pool)
    cache_key = (proxy or "", timeout_key)
    cached = _http_client_cache.get(cache_key)
    if cached is not None:
        return cached
    kw: dict = {"headers": _CUSTOM_HEADERS, "follow_redirects": True}
    if timeout:
        kw["timeout"] = timeout
    if proxy:
        kw["proxy"] = _normalize_proxy_url(proxy)
    pair = (httpx.Client(**kw), httpx.AsyncClient(**kw))
    _http_client_cache[cache_key] = pair
    return pair


def _channel_timeout(channel: dict) -> httpx.Timeout:
    """Per-channel read timeout. ``channel['timeout']`` (seconds) overrides the
    read leg only; 0/absent falls back to the global ``_LLM_TIMEOUT``. The
    connect/write/pool legs keep the global values."""
    t = channel.get("timeout")
    if not t:
        return _LLM_TIMEOUT
    return httpx.Timeout(connect=15.0, read=float(t), write=30.0, pool=30.0)


# Status codes that indicate a deterministic config/request error — failing over
# to another channel won't help and would needlessly trip every channel's
# cooldown. 429 (rate limit) is intentionally excluded: it IS worth failing over.
_FATAL_STATUS_CODES = frozenset({400, 401, 403, 404, 422})


def _extract_status_code(exc: Exception) -> int | None:
    """Best-effort HTTP status from a LangChain/openai/httpx exception.

    LangChain wraps the underlying openai/httpx error, so the status may live on
    the exception itself, on its ``.response``, or on a chained cause/context.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        code = getattr(cur, "status_code", None)
        if isinstance(code, int):
            return code
        resp_code = getattr(getattr(cur, "response", None), "status_code", None)
        if isinstance(resp_code, int):
            return resp_code
        cur = cur.__cause__ or cur.__context__
    return None


def _is_fatal_config_error(exc: Exception) -> bool:
    """True for a deterministic 4xx (except 429) — don't fail over on these."""
    return _extract_status_code(exc) in _FATAL_STATUS_CODES


def _reraise_if_fatal(exc: Exception, channel: dict) -> None:
    """Re-raise a deterministic 4xx instead of failing over.

    Failover can't fix a config/request error and would needlessly trip every
    channel's cooldown. Shared by all three ResilientChatModel retry loops.
    """
    if _is_fatal_config_error(exc):
        logger.error("LLM channel '%s' fatal config error, not failing over: %s", channel["name"], exc)
        raise exc


def _is_transient_error(exc: Exception) -> bool:
    """Worth one same-channel retry before failing over: a 5xx/429/408/409 or a
    network/transport/timeout error. Fatal 4xx are already filtered by _reraise_if_fatal."""
    code = _extract_status_code(exc)
    if code is not None:
        return code in _RETRYABLE_STATUS
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, (httpx.TimeoutException, httpx.TransportError)):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


# ── ResilientChatModel — transparent failover wrapper ──

class ResilientChatModel:
    """Drop-in replacement for ChatOpenAI with multi-channel auto-failover."""

    def __init__(self, tier: str | None = None, effort_override: str | None = None):
        self._bind_args: tuple = ()
        self._bind_kwargs: dict = {}
        self._tier = tier
        # Per-call reasoning_effort override. Takes precedence over the channel's own
        # setting, letting one code path (e.g. summary generation) use a lower effort
        # without touching the global/chat config.
        self._effort_override = effort_override

    def bind_tools(self, tools, **kwargs):
        """Return a new ResilientChatModel that binds tools on each underlying LLM."""
        bound = ResilientChatModel(tier=self._tier, effort_override=self._effort_override)
        bound._bind_args = (tools,)
        bound._bind_kwargs = kwargs
        return bound

    def _make_and_bind(self, channel: dict) -> ChatOpenAI:
        llm = self._make_llm(channel)
        if self._bind_args:
            return llm.bind_tools(*self._bind_args, **self._bind_kwargs)
        return llm

    def _make_llm(self, channel: dict) -> ChatOpenAI:
        sync_c, async_c = _build_http_clients(channel.get("proxy", ""), timeout=_channel_timeout(channel))
        effort = _resolve_reasoning_effort(self._effort_override or channel.get("reasoning_effort"))
        model_kwargs = {"extra_body": {"reasoning_effort": effort}} if effort else {}
        return ChatOpenAI(
            model=channel.get("model", ""),
            api_key=channel.get("api_key", ""),
            base_url=channel.get("api_base", ""),
            temperature=float(channel.get("temperature", 0.7)),
            max_tokens=int(channel.get("max_tokens") or 16384),
            http_client=sync_c,
            http_async_client=async_c,
            default_headers=_CUSTOM_HEADERS,
            model_kwargs=model_kwargs,
        )

    def invoke(self, messages, **kwargs):
        from backend.channel_manager import get_channel, get_next_channel, report_error, report_success
        tried: set[str] = set()
        channel = get_channel("llm", tier=self._tier)
        while channel:
            for attempt in range(_MAX_SAME_CHANNEL_ATTEMPTS):
                try:
                    result = self._make_and_bind(channel).invoke(messages, **kwargs)
                    report_success("llm", channel["id"])
                    return result
                except Exception as e:
                    _reraise_if_fatal(e, channel)
                    if attempt + 1 < _MAX_SAME_CHANNEL_ATTEMPTS and _is_transient_error(e):
                        logger.warning(
                            "LLM channel '%s' transient error (attempt %d/%d), retrying same channel: %s",
                            channel["name"], attempt + 1, _MAX_SAME_CHANNEL_ATTEMPTS, e,
                        )
                        time.sleep(_SAME_CHANNEL_BACKOFF_SECONDS)
                        continue
                    logger.warning("LLM channel '%s' invoke failed: %s", channel["name"], e)
                    report_error("llm", channel["id"])
                    tried.add(channel["id"])
                    break
            channel = get_next_channel("llm", tried, tier=self._tier)
        raise RuntimeError("All LLM channels exhausted")

    async def ainvoke(self, messages, **kwargs):
        from backend.channel_manager import get_channel, get_next_channel, report_error, report_success
        tried: set[str] = set()
        channel = get_channel("llm", tier=self._tier)
        while channel:
            for attempt in range(_MAX_SAME_CHANNEL_ATTEMPTS):
                try:
                    result = await self._make_and_bind(channel).ainvoke(messages, **kwargs)
                    report_success("llm", channel["id"])
                    return result
                except Exception as e:
                    _reraise_if_fatal(e, channel)
                    if attempt + 1 < _MAX_SAME_CHANNEL_ATTEMPTS and _is_transient_error(e):
                        logger.warning(
                            "LLM channel '%s' transient error (attempt %d/%d), retrying same channel: %s",
                            channel["name"], attempt + 1, _MAX_SAME_CHANNEL_ATTEMPTS, e,
                        )
                        await asyncio.sleep(_SAME_CHANNEL_BACKOFF_SECONDS)
                        continue
                    logger.warning("LLM channel '%s' ainvoke failed: %s", channel["name"], e)
                    report_error("llm", channel["id"])
                    tried.add(channel["id"])
                    break
            channel = get_next_channel("llm", tried, tier=self._tier)
        raise RuntimeError("All LLM channels exhausted")

    async def astream(self, messages, **kwargs):
        from backend.channel_manager import get_channel, get_next_channel, report_error, report_success
        tried: set[str] = set()
        channel = get_channel("llm", tier=self._tier)
        while channel:
            # Phase 1 — fetch the first chunk. Safe to fail over: nothing has been
            # yielded to the client yet.
            try:
                llm = self._make_and_bind(channel)
                aiter = llm.astream(messages, **kwargs).__aiter__()
                first_chunk = await aiter.__anext__()
            except StopAsyncIteration:
                # Empty stream — treat as a successful (empty) response.
                report_success("llm", channel["id"])
                return
            except Exception as e:
                _reraise_if_fatal(e, channel)
                logger.warning("LLM channel '%s' astream failed before first chunk: %s", channel["name"], e)
                report_error("llm", channel["id"])
                tried.add(channel["id"])
                channel = get_next_channel("llm", tried, tier=self._tier)
                continue
            # Phase 2 — stream is committed. Once the first chunk is out we must NOT
            # fail over: replaying the prompt on another channel would concatenate a
            # fresh full answer onto the partial one already sent. Let any mid-stream
            # error propagate to the caller instead.
            report_success("llm", channel["id"])
            yield first_chunk
            async for chunk in aiter:
                yield chunk
            return
        raise RuntimeError("All LLM channels exhausted")


# ── Public API (unchanged signatures) ──

def get_langchain_llm(tier: str | None = None, reasoning_effort: str | None = None):
    """LangChain ChatModel for LangGraph nodes. Uses multi-channel if configured.

    Args:
        tier: "small" | "large" | None. When set, only channels tagged with the matching
              tier are used. Falls back to any-tier with a warning if the requested tier
              has no available channels (handled inside channel_manager).
        reasoning_effort: per-call override for the model's reasoning effort. Overrides the
              channel/global setting for this LLM only — e.g. summary generation can request
              "low" without lowering the chat path's effort. None/"" keeps the configured value.
    """
    from backend.channel_manager import has_channels
    if has_channels("llm"):
        return ResilientChatModel(tier=tier, effort_override=reasoning_effort or None)
    sync_c, async_c = _build_http_clients()
    effort = _resolve_reasoning_effort(reasoning_effort or get_effective("llm", "reasoning_effort"))
    model_kwargs = {"extra_body": {"reasoning_effort": effort}} if effort else {}
    if tier is not None:
        logger.warning(
            "get_langchain_llm(tier=%s) requested but no channel pool configured; "
            "using single-channel legacy config.", tier,
        )
    return ChatOpenAI(
        model=get_effective("llm", "model"),
        api_key=get_effective("llm", "api_key"),
        base_url=get_effective("llm", "api_base"),
        temperature=float(get_effective("llm", "temperature") or 0.7),
        max_tokens=16384,
        http_client=sync_c,
        http_async_client=async_c,
        default_headers=_CUSTOM_HEADERS,
        model_kwargs=model_kwargs,
    )


def get_llama_llm():
    """LlamaIndex LLM (singleton, auto-invalidates on config change)."""
    global _llama_llm_instance, _llama_config_version
    ver = get_config_version()
    if _llama_llm_instance is None or _llama_config_version != ver:
        from backend.channel_manager import get_channel, has_channels
        if has_channels("llm"):
            ch = get_channel("llm")
            if ch:
                add_kw: dict = {"extra_headers": _CUSTOM_HEADERS}
                effort = _resolve_reasoning_effort(ch.get("reasoning_effort"))
                if effort:
                    add_kw["extra_body"] = {"reasoning_effort": effort}
                _llama_llm_instance = OpenAILike(
                    model=ch["model"], api_key=ch["api_key"], api_base=ch["api_base"],
                    temperature=float(ch.get("temperature", 0.7)),
                    max_tokens=int(ch.get("max_tokens") or 16384),
                    timeout=float(ch.get("timeout")) if ch.get("timeout") else 240.0,
                    is_chat_model=True,
                    additional_kwargs=add_kw,
                )
                _llama_config_version = ver
                return _llama_llm_instance
        add_kw: dict = {"extra_headers": _CUSTOM_HEADERS}
        effort = _resolve_reasoning_effort(get_effective("llm", "reasoning_effort"))
        if effort:
            add_kw["extra_body"] = {"reasoning_effort": effort}
        _llama_llm_instance = OpenAILike(
            model=get_effective("llm", "model"),
            api_key=get_effective("llm", "api_key"),
            api_base=get_effective("llm", "api_base"),
            temperature=float(get_effective("llm", "temperature") or 0.7),
            max_tokens=16384,
            timeout=240.0,
            is_chat_model=True,
            additional_kwargs=add_kw,
        )
        _llama_config_version = ver
    return _llama_llm_instance


def get_embedding():
    """Embedding model (singleton, auto-invalidates on config change)."""
    global _embedding_instance, _embedding_config_version
    ver = get_config_version()
    if _embedding_instance is None or _embedding_config_version != ver:
        _embedding_instance = _create_embedding()
        _embedding_config_version = ver
    return _embedding_instance


def _create_embedding():
    """Create a fresh embedding instance — channel-aware."""
    from backend.config import settings
    from backend.channel_manager import get_channel, has_channels

    if has_channels("embedding"):
        ch = get_channel("embedding")
        if ch:
            backend = ch.get("backend", "api")
            if backend == "api":
                from llama_index.embeddings.openai import OpenAIEmbedding
                sync_c, _ = _build_http_clients(ch.get("proxy", ""), timeout=_EMBED_TIMEOUT)
                kwargs = {
                    "model_name": ch.get("api_model", ""),
                    "api_key": ch.get("api_key", ""),
                    "http_client": sync_c,
                    "embed_batch_size": 10,
                    # Fail fast: cap the SDK's internal retry/backoff (default
                    # max_retries=10 + tenacity) so a single query embed can't
                    # blow past the outer 60s retrieval timeout and leak threads.
                    "max_retries": 1,
                    "timeout": 20.0,
                }
                if ch.get("api_base"):
                    kwargs["api_base"] = ch["api_base"]
                return OpenAIEmbedding(**kwargs)

    backend = get_effective("embedding", "backend") or ""
    api_base = get_effective("embedding", "api_base")
    api_key = get_effective("embedding", "api_key")

    if backend.strip().lower() == "api" or (not backend and (api_base or api_key)):
        from llama_index.embeddings.openai import OpenAIEmbedding
        model_name = get_effective("embedding", "api_model")
        if not model_name:
            raise RuntimeError("Embedding API model is required when backend=api")
        sync_c, _ = _build_http_clients(timeout=_EMBED_TIMEOUT)
        kwargs = {
            "model_name": model_name,
            "api_key": api_key,
            "http_client": sync_c,
            "embed_batch_size": 10,
            # Same fail-fast cap as the channel-pool branch; the previous
            # timeout=None disabled httpx timeouts entirely (could hang forever).
            "max_retries": 1,
            "timeout": 20.0,
        }
        if api_base:
            kwargs["api_base"] = api_base
        return OpenAIEmbedding(**kwargs)
    else:
        try:
            from llama_index.embeddings.huggingface import HuggingFaceEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "Local embeddings require optional dependencies. "
                "Install `pip install -r requirements.local-embedding.txt` "
                "and a torch build that matches your environment."
            ) from exc

        local_path = get_effective("embedding", "local_path")
        local_model = get_effective("embedding", "local_model")

        if local_path:
            return HuggingFaceEmbedding(model_name=str(local_path))
        elif local_model:
            return HuggingFaceEmbedding(model_name=local_model)
        else:
            model_path = settings.local_embedding_model_path()
            model_name = settings.local_embedding_model_name()
            if model_path is not None:
                return HuggingFaceEmbedding(model_name=str(model_path))
            elif model_name:
                return HuggingFaceEmbedding(model_name=model_name)
            else:
                raise RuntimeError(
                    "LOCAL_EMBEDDING_MODEL or LOCAL_EMBEDDING_PATH is required "
                    "when EMBEDDING_BACKEND=local"
                )


def invalidate_singletons():
    """Force recreation of cached LLM/embedding instances on next call."""
    global _llama_llm_instance, _embedding_instance
    global _llama_config_version, _embedding_config_version
    _llama_llm_instance = None
    _embedding_instance = None
    _llama_config_version = -1
    _embedding_config_version = -1

    # 记忆库向量后端单例也随配置失效（embedding 维度/通道变更后需重连）。
    try:
        from backend.vector_store import reset_vector_store
        reset_vector_store()
    except Exception:
        pass

    # 知识库 Qdrant 状态（client / 健康标志 / 维度缓存）一并失效，使换 embedding
    # 通道后维度校验与降级判定基于新配置。
    try:
        from backend.indexer import reset_qdrant_state
        reset_qdrant_state()
    except Exception:
        pass

    try:
        from llama_index.core import Settings as LlamaSettings
        LlamaSettings.llm = get_llama_llm()
        LlamaSettings.embed_model = get_embedding()
    except Exception:
        pass
