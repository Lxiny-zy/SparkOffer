"""LLM / Embedding provider with multi-channel failover support."""
import logging
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
_LLM_TIMEOUT = httpx.Timeout(connect=15.0, read=240.0, write=30.0, pool=30.0)
_EMBED_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=30.0)  # Embedding 超时配置

_REASONING_EFFORTS = {"minimal", "low", "medium", "high"}


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


def _build_http_clients(proxy: str = "", *, timeout: httpx.Timeout | None = _LLM_TIMEOUT):
    """Return (sync_client, async_client) with optional proxy (http/https/socks5)."""
    kw: dict = {"headers": _CUSTOM_HEADERS, "follow_redirects": True}
    if timeout:
        kw["timeout"] = timeout
    if proxy:
        kw["proxy"] = _normalize_proxy_url(proxy)
    return httpx.Client(**kw), httpx.AsyncClient(**kw)


# ── ResilientChatModel — transparent failover wrapper ──

class ResilientChatModel:
    """Drop-in replacement for ChatOpenAI with multi-channel auto-failover."""

    def __init__(self, tier: str | None = None):
        self._bind_args: tuple = ()
        self._bind_kwargs: dict = {}
        self._tier = tier

    def bind_tools(self, tools, **kwargs):
        """Return a new ResilientChatModel that binds tools on each underlying LLM."""
        bound = ResilientChatModel(tier=self._tier)
        bound._bind_args = (tools,)
        bound._bind_kwargs = kwargs
        return bound

    def _make_and_bind(self, channel: dict) -> ChatOpenAI:
        llm = self._make_llm(channel)
        if self._bind_args:
            return llm.bind_tools(*self._bind_args, **self._bind_kwargs)
        return llm

    @staticmethod
    def _make_llm(channel: dict) -> ChatOpenAI:
        sync_c, async_c = _build_http_clients(channel.get("proxy", ""))
        effort = _resolve_reasoning_effort(channel.get("reasoning_effort"))
        model_kwargs = {"extra_body": {"reasoning_effort": effort}} if effort else {}
        return ChatOpenAI(
            model=channel.get("model", ""),
            api_key=channel.get("api_key", ""),
            base_url=channel.get("api_base", ""),
            temperature=float(channel.get("temperature", 0.7)),
            max_tokens=4096,
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
            try:
                result = self._make_and_bind(channel).invoke(messages, **kwargs)
                report_success("llm", channel["id"])
                return result
            except Exception as e:
                logger.warning("LLM channel '%s' invoke failed: %s", channel["name"], e)
                report_error("llm", channel["id"])
                tried.add(channel["id"])
                channel = get_next_channel("llm", tried, tier=self._tier)
        raise RuntimeError("All LLM channels exhausted")

    async def ainvoke(self, messages, **kwargs):
        from backend.channel_manager import get_channel, get_next_channel, report_error, report_success
        tried: set[str] = set()
        channel = get_channel("llm", tier=self._tier)
        while channel:
            try:
                result = await self._make_and_bind(channel).ainvoke(messages, **kwargs)
                report_success("llm", channel["id"])
                return result
            except Exception as e:
                logger.warning("LLM channel '%s' ainvoke failed: %s", channel["name"], e)
                report_error("llm", channel["id"])
                tried.add(channel["id"])
                channel = get_next_channel("llm", tried, tier=self._tier)
        raise RuntimeError("All LLM channels exhausted")

    async def astream(self, messages, **kwargs):
        from backend.channel_manager import get_channel, get_next_channel, report_error, report_success
        tried: set[str] = set()
        channel = get_channel("llm", tier=self._tier)
        while channel:
            try:
                llm = self._make_and_bind(channel)
                aiter = llm.astream(messages, **kwargs).__aiter__()
                first_chunk = await aiter.__anext__()
                report_success("llm", channel["id"])
                yield first_chunk
                async for chunk in aiter:
                    yield chunk
                return
            except Exception as e:
                logger.warning("LLM channel '%s' astream failed: %s", channel["name"], e)
                report_error("llm", channel["id"])
                tried.add(channel["id"])
                channel = get_next_channel("llm", tried, tier=self._tier)
        raise RuntimeError("All LLM channels exhausted")


# ── Public API (unchanged signatures) ──

def get_langchain_llm(tier: str | None = None):
    """LangChain ChatModel for LangGraph nodes. Uses multi-channel if configured.

    Args:
        tier: "small" | "large" | None. When set, only channels tagged with the matching
              tier are used. Falls back to any-tier with a warning if the requested tier
              has no available channels (handled inside channel_manager).
    """
    from backend.channel_manager import has_channels
    if has_channels("llm"):
        return ResilientChatModel(tier=tier)
    sync_c, async_c = _build_http_clients()
    effort = _resolve_reasoning_effort(get_effective("llm", "reasoning_effort"))
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
        max_tokens=4096,
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
                    "embed_batch_size": 1,
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
        sync_c, _ = _build_http_clients(timeout=None)
        kwargs = {
            "model_name": model_name,
            "api_key": api_key,
            "http_client": sync_c,
            "embed_batch_size": 1,
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

    try:
        from llama_index.core import Settings as LlamaSettings
        LlamaSettings.llm = get_llama_llm()
        LlamaSettings.embed_model = get_embedding()
    except Exception:
        pass
