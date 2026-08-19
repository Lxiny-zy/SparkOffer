"""AI settings routes — runtime config, connection tests, multi-channel management."""
import httpx as _httpx
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from backend.models import (
    AIConfigUpdate, TestLLMRequest, TestEmbeddingRequest,
    ChannelsConfig, LLMChannelConfig, EmbeddingChannelConfig, RerankerChannelConfig,
    TestChannelRequest, TuningConfig,
)
from backend.auth import require_owner
from backend.routers.auth import client_ip
from backend.storage.audit import log_event
from backend.utils.outbound import (
    OutboundTargetError,
    redact_url_credentials,
    validate_probe_targets,
)

router = APIRouter(prefix="/api")


def _validate_test_network(api_base: str = "", proxy: str = "") -> str | None:
    """Validate an operator-supplied probe destination before opening sockets."""
    try:
        validate_probe_targets(api_base, proxy)
    except OutboundTargetError as exc:
        return str(exc)
    return None


def _resolve_test_api_key(section: str, channel: dict) -> str:
    """Resolve a masked key from the persisted channel for a connection test."""
    from backend.ai_config import SECRET_MASK, is_secret_mask

    supplied = channel.get("api_key")
    if isinstance(supplied, str) and supplied and not is_secret_mask(supplied):
        return supplied
    if supplied not in (None, "", SECRET_MASK):
        return str(supplied)
    channel_id = str(channel.get("id") or "")
    if channel_id:
        from backend.channel_manager import get_all_channels
        for stored in get_all_channels(section):
            if str(stored.get("id") or "") != channel_id:
                continue
            keys = stored.get("keys") or []
            if keys:
                return str(keys[0])
    keys = channel.get("keys") or []
    for key in keys:
        if isinstance(key, str) and key and not is_secret_mask(key):
            return key
    return ""


def _redact_probe_error(exc: Exception, *secrets: object) -> str:
    """Keep provider errors useful without reflecting credentials to the owner UI."""
    text = str(exc)
    for secret in secrets:
        if secret is None:
            continue
        value = str(secret)
        if value:
            text = text.replace(value, "[redacted]")
            # A proxy URL may be normalized/percent-encoded by httpx before it
            # appears in an exception. Replace both the raw URL and a safe form.
            safe_url = redact_url_credentials(value)
            if safe_url != value:
                text = text.replace(safe_url, "[redacted-url]")
            try:
                parsed = urlsplit(value)
                for credential in (parsed.username, parsed.password):
                    if credential:
                        text = text.replace(credential, "[redacted]")
            except ValueError:
                pass
    return text[:1000]


@router.get("/settings/ai")
def get_ai_settings(user_id: str = Depends(require_owner)):
    from backend.ai_config import get_all_effective
    return get_all_effective()


@router.put("/settings/ai")
def update_ai_settings(req: AIConfigUpdate, request: Request, user_id: str = Depends(require_owner)):
    from backend.ai_config import save_ai_config
    from backend.llm_provider import invalidate_singletons

    config = req.model_dump(exclude_none=True)
    # Preserve valid falsy values such as temperature=0.
    config = {k: v for k, v in config.items() if v != ""}
    save_ai_config(config)
    invalidate_singletons()
    log_event("ai_config_updated", user_id=user_id, ip=client_ip(request),
              detail={"sections": list(config.keys())})
    return {"ok": True}


@router.post("/settings/ai/test/llm")
async def test_llm_connection(req: TestLLMRequest, user_id: str = Depends(require_owner)):
    network_error = _validate_test_network(req.api_base)
    if network_error:
        return {"ok": False, "error": network_error}
    try:
        from langchain_openai import ChatOpenAI
        from backend.llm_provider import _resolve_reasoning_effort
        http_client = _httpx.Client(
            headers={"User-Agent": "curl/7.88.1"}, follow_redirects=False
        )
        effort = _resolve_reasoning_effort(req.reasoning_effort)
        extra_body = {"reasoning_effort": effort} if effort else None
        llm = ChatOpenAI(
            model=req.model,
            api_key=req.api_key,
            base_url=req.api_base,
            temperature=req.temperature,
            http_client=http_client,
            default_headers={"User-Agent": "curl/7.88.1"},
            max_tokens=20,
            extra_body=extra_body,
        )
        resp = await llm.ainvoke("Say hello in one word.")
        return {"ok": True, "message": resp.content[:100]}
    except Exception as e:
        return {"ok": False, "error": _redact_probe_error(e, req.api_key)}


@router.post("/settings/ai/test/embedding")
async def test_embedding_connection(req: TestEmbeddingRequest, user_id: str = Depends(require_owner)):
    if req.backend.strip().lower() == "api" and req.api_base.strip():
        network_error = _validate_test_network(req.api_base)
        if network_error:
            return {"ok": False, "error": network_error}
    try:
        if req.backend == "api":
            from llama_index.embeddings.openai import OpenAIEmbedding
            kwargs = {
                "model_name": req.api_model,
                "api_key": req.api_key,
                "http_client": _httpx.Client(
                    headers={"User-Agent": "curl/7.88.1"}, follow_redirects=False,
                ),
            }
            if req.api_base:
                kwargs["api_base"] = req.api_base
            embed = OpenAIEmbedding(**kwargs)
            result = await embed.aget_text_embedding("hello")
            return {"ok": True, "dimensions": len(result)}
        else:
            return {"ok": False, "error": "Local embedding test is not supported via API. Requires server-side model files."}
    except Exception as e:
        return {"ok": False, "error": _redact_probe_error(e, req.api_key)}


# ── Multi-Channel Endpoints ──

@router.get("/settings/ai/channels")
def get_channels_config(user_id: str = Depends(require_owner)):
    from backend.ai_config import get_channels
    from backend.channel_manager import get_health

    return {
        "llm": {"channels": get_channels("llm"), "health": get_health("llm")},
        "embedding": {"channels": get_channels("embedding"), "health": get_health("embedding")},
        "reranker": {"channels": get_channels("reranker"), "health": get_health("reranker")},
    }


def _ensure_embedding_models_match(channels: list[EmbeddingChannelConfig]) -> None:
    emb_models = {
        ch.api_model for ch in channels
        if ch.enabled and ch.backend.strip().lower() == "api" and ch.api_model
    }
    if len(emb_models) > 1:
        raise HTTPException(400, f"All embedding channels must use the same model. Found: {emb_models}")


_CHANNEL_SECTION_MODELS = {
    "llm": LLMChannelConfig,
    "embedding": EmbeddingChannelConfig,
    "reranker": RerankerChannelConfig,
}


def _validate_channel_section(section: str, channels: list[dict]):
    model = _CHANNEL_SECTION_MODELS.get(section)
    if model is None:
        raise HTTPException(400, f"Unknown channel section: {section}")
    try:
        parsed = [model.model_validate(ch) for ch in channels]
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc

    errors: list[str] = []
    for index, channel in enumerate(parsed):
        # Keep persisted values canonical so provider selection and validation
        # use the same branch even when a client sends "API" / "LOCAL".
        if section == "embedding":
            channel.backend = channel.backend.strip().lower()
        if not channel.enabled:
            continue
        label = channel.name.strip() or f"channel {index + 1}"
        # Persisted runtime channels may intentionally target a local provider
        # (Ollama/LM Studio), so allow private addresses here while still
        # rejecting malformed URLs and public plaintext HTTP. The explicit
        # connection-test endpoint below applies the stricter public-only
        # policy because it performs an operator-requested probe immediately.
        if channel.api_base.strip() or channel.proxy.strip():
            try:
                from backend.utils.outbound import validate_api_base, validate_proxy
                if channel.proxy.strip():
                    validate_proxy(channel.proxy, allow_private=True, resolve_host=False)
                if channel.api_base.strip():
                    validate_api_base(
                        channel.api_base,
                        allow_private=True,
                        allow_http_loopback=True,
                        resolve_host=False,
                    )
            except OutboundTargetError as exc:
                errors.append(f"{label}: {exc}")
                continue
        if section == "llm":
            if not channel.model.strip():
                errors.append(f"{label}: model is required when enabled")
            if not channel.api_base.strip() and not any(k.strip() for k in channel.keys):
                errors.append(f"{label}: api_base or an API key is required when enabled")
        elif section == "embedding":
            backend = channel.backend.strip().lower()
            if backend == "api":
                if not channel.api_model.strip():
                    errors.append(f"{label}: api_model is required for an enabled API channel")
                if not channel.api_base.strip() and not any(k.strip() for k in channel.keys):
                    errors.append(f"{label}: api_base or an API key is required for an enabled API channel")
            elif backend == "local":
                if not channel.local_model.strip() and not channel.local_path.strip():
                    errors.append(f"{label}: local_model or local_path is required for an enabled local channel")
            else:
                errors.append(f"{label}: backend must be 'api' or 'local'")
        elif section == "reranker":
            if not channel.api_base.strip():
                errors.append(f"{label}: api_base is required when enabled")
            if not any(k.strip() for k in channel.keys):
                errors.append(f"{label}: at least one API key is required when enabled")

    if errors:
        raise HTTPException(422, {
            "message": "Invalid enabled channel configuration",
            "errors": errors,
        })
    if section == "embedding":
        _ensure_embedding_models_match(parsed)
    return parsed


@router.put("/settings/ai/channels")
def update_channels_config(req: ChannelsConfig, request: Request, user_id: str = Depends(require_owner)):
    from backend.ai_config import save_channels
    from backend.llm_provider import invalidate_singletons

    parsed = {
        section: _validate_channel_section(
            section,
            [channel.model_dump() for channel in getattr(req, section)],
        )
        for section in _CHANNEL_SECTION_MODELS
    }
    config = {
        section: [channel.model_dump() for channel in channels]
        for section, channels in parsed.items()
    }
    save_channels(config)
    invalidate_singletons()
    log_event("channels_updated", user_id=user_id, ip=client_ip(request),
              detail={s: [c.get("name", "?") for c in config[s]] for s in config})
    return {"ok": True}


@router.put("/settings/ai/channels/{section}")
def update_channel_section(section: str, request: Request,
                           channels: list[dict] = Body(...),
                           user_id: str = Depends(require_owner)):
    """Update exactly one channel section.

    The settings page renders LLM / embedding / reranker as independent managers.
    Saving a full stale snapshot from any one manager can roll back another
    section. This endpoint keeps the old full-save API for compatibility while
    giving each manager an atomic per-section write path.
    """
    from backend.ai_config import save_channels
    from backend.llm_provider import invalidate_singletons

    parsed = _validate_channel_section(section, channels)
    config = {section: [ch.model_dump() for ch in parsed]}
    save_channels(config)
    invalidate_singletons()
    log_event("channel_section_updated", user_id=user_id, ip=client_ip(request),
              detail={section: [c.get("name", "?") for c in config[section]]})
    return {"ok": True, "section": section}


@router.post("/settings/ai/channels/test")
async def test_channel(req: TestChannelRequest, user_id: str = Depends(require_owner)):
    ch = req.channel
    section = req.section

    api_base = str(ch.get("api_base", "") or "")
    proxy = str(ch.get("proxy", "") or "")
    if section not in {"llm", "embedding", "reranker"}:
        return {"ok": False, "error": f"Unknown section: {section}"}
    if section == "embedding" and str(ch.get("backend", "api")).strip().lower() == "local":
        return {"ok": False, "error": "Local embedding test is not supported via API. Requires server-side model files."}
    should_validate_base = bool(api_base.strip() or proxy.strip())
    network_error = (
        _validate_test_network(api_base, proxy)
        if should_validate_base else None
    )
    if network_error:
        return {"ok": False, "error": network_error}
    proxy = proxy or None
    test_api_key = _resolve_test_api_key(section, ch)

    if section == "llm":
        try:
            from langchain_openai import ChatOpenAI
            from backend.llm_provider import _normalize_proxy_url, _resolve_reasoning_effort
            client_kw: dict = {"headers": {"User-Agent": "curl/7.88.1"}, "follow_redirects": False}
            if proxy:
                client_kw["proxy"] = _normalize_proxy_url(proxy)
            effort = _resolve_reasoning_effort(ch.get("reasoning_effort"))
            extra_body = {"reasoning_effort": effort} if effort else None
            llm = ChatOpenAI(
                model=ch.get("model", ""),
                api_key=test_api_key,
                base_url=api_base,
                temperature=float(ch.get("temperature", 0.7)),
                http_client=_httpx.Client(**client_kw),
                default_headers={"User-Agent": "curl/7.88.1"},
                max_tokens=20,
                extra_body=extra_body,
            )
            resp = await llm.ainvoke("Say hello in one word.")
            return {"ok": True, "message": resp.content[:100]}
        except Exception as e:
            return {"ok": False, "error": _redact_probe_error(e, test_api_key, proxy, *(ch.get("keys") or []))}

    elif section == "embedding":
        try:
            from llama_index.embeddings.openai import OpenAIEmbedding
            from backend.llm_provider import _normalize_proxy_url
            emb_kw: dict = {"headers": {"User-Agent": "curl/7.88.1"}, "follow_redirects": False}
            if proxy:
                emb_kw["proxy"] = _normalize_proxy_url(proxy)
            kwargs = {
                "model_name": ch.get("api_model", ""),
                "api_key": test_api_key,
                "http_client": _httpx.Client(**emb_kw),
            }
            if api_base:
                kwargs["api_base"] = api_base
            embed = OpenAIEmbedding(**kwargs)
            result = await embed.aget_text_embedding("hello")
            return {"ok": True, "dimensions": len(result)}
        except Exception as e:
            return {"ok": False, "error": _redact_probe_error(e, test_api_key, proxy, *(ch.get("keys") or []))}

    elif section == "reranker":
        try:
            from backend.llm_provider import _normalize_proxy_url
            api_base = (ch.get("api_base", "") or "").rstrip("/")
            if not api_base:
                return {"ok": False, "error": "API Base URL 不能为空"}
            url = api_base if api_base.endswith("/rerank") else f"{api_base}/rerank"
            client_kw: dict = {"headers": {"User-Agent": "curl/7.88.1"}, "follow_redirects": False}
            if proxy:
                client_kw["proxy"] = _normalize_proxy_url(proxy)
            async with _httpx.AsyncClient(timeout=20.0, **client_kw) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {test_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": ch.get("api_model", ""),
                        "query": "什么是向量检索",
                        "documents": ["向量检索通过语义相似度匹配文档", "今天天气晴朗适合出门"],
                        "top_n": 2,
                        "return_documents": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            n = len(data.get("results", []))
            return {"ok": True, "message": f"Rerank OK — {n} 条结果"}
        except _httpx.HTTPStatusError as e:
            detail = _redact_probe_error(
                Exception(e.response.text[:200]),
                test_api_key,
                proxy,
                *(ch.get("keys") or []),
            )
            return {"ok": False, "error": f"HTTP {e.response.status_code}: {detail}"}
        except Exception as e:
            return {"ok": False, "error": _redact_probe_error(e, test_api_key, proxy, *(ch.get("keys") or []))}

    return {"ok": False, "error": f"Unsupported section: {section}"}


@router.get("/settings/ai/channels/health")
def get_channels_health(user_id: str = Depends(require_owner)):
    from backend.channel_manager import get_health
    return {
        "llm": get_health("llm"),
        "embedding": get_health("embedding"),
        "reranker": get_health("reranker"),
    }


# ── Runtime tuning (context budget + retrieval) ──

@router.get("/settings/tuning")
def get_tuning_settings(user_id: str = Depends(require_owner)):
    """Resolved tuning values + defaults + retrieval presets for the settings UI."""
    from backend.ai_config import get_tuning_config
    return get_tuning_config()


@router.put("/settings/tuning")
def update_tuning_settings(req: TuningConfig, request: Request, user_id: str = Depends(require_owner)):
    """Persist tuning overlay. Read at call time everywhere, so this hot-applies;
    invalidate_singletons() additionally refreshes the cached LlamaIndex LLM whose
    max_tokens is built at construction time."""
    from backend.ai_config import save_tuning
    from backend.llm_provider import invalidate_singletons

    save_tuning(req.model_dump())
    invalidate_singletons()
    log_event("tuning_updated", user_id=user_id, ip=client_ip(request))
    return {"ok": True}


# ── Admin: audit log + user list (owner only) ──

@router.get("/admin/audit")
def admin_audit_logs(
                     event: str | None = Query(default=None, max_length=100),
                     limit: int = Query(default=100, ge=1, le=500),
                     offset: int = Query(default=0, ge=0),
                     user_id: str = Depends(require_owner)):
    from backend.storage.audit import list_audit_logs, list_event_names
    result = list_audit_logs(event=event, limit=limit, offset=offset)
    result["events"] = list_event_names()
    return result


@router.get("/admin/users")
def admin_list_users(user_id: str = Depends(require_owner)):
    from backend.storage.database import get_db
    from backend.auth import is_owner
    conn = get_db()
    rows = conn.execute(
        "SELECT id, email, name, created_at FROM users ORDER BY created_at ASC"
    ).fetchall()
    return {"users": [dict(r) | {"is_owner": is_owner(r["id"])} for r in rows]}
