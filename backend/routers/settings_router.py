"""AI settings routes — runtime config, connection tests, multi-channel management."""
import httpx as _httpx

from fastapi import APIRouter, Depends, HTTPException

from backend.models import (
    AIConfigUpdate, TestLLMRequest, TestEmbeddingRequest,
    TestASRRequest, TestQiniuRequest,
    ChannelsConfig, TestChannelRequest,
)
from backend.auth import get_current_user

router = APIRouter(prefix="/api")


@router.get("/settings/ai")
def get_ai_settings(user_id: str = Depends(get_current_user)):
    from backend.ai_config import get_all_effective
    return get_all_effective()


@router.put("/settings/ai")
def update_ai_settings(req: AIConfigUpdate, user_id: str = Depends(get_current_user)):
    from backend.ai_config import save_ai_config
    from backend.llm_provider import invalidate_singletons

    config = req.model_dump(exclude_none=True)
    config = {k: v for k, v in config.items() if v}
    save_ai_config(config)
    invalidate_singletons()
    return {"ok": True}


@router.post("/settings/ai/test/llm")
async def test_llm_connection(req: TestLLMRequest, user_id: str = Depends(get_current_user)):
    try:
        from langchain_openai import ChatOpenAI
        http_client = _httpx.Client(
            headers={"User-Agent": "curl/7.88.1"}, follow_redirects=True
        )
        llm = ChatOpenAI(
            model=req.model,
            api_key=req.api_key,
            base_url=req.api_base,
            temperature=req.temperature,
            http_client=http_client,
            default_headers={"User-Agent": "curl/7.88.1"},
            max_tokens=20,
        )
        resp = await llm.ainvoke("Say hello in one word.")
        return {"ok": True, "message": resp.content[:100]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/settings/ai/test/embedding")
async def test_embedding_connection(req: TestEmbeddingRequest, user_id: str = Depends(get_current_user)):
    try:
        if req.backend == "api":
            from llama_index.embeddings.openai import OpenAIEmbedding
            kwargs = {
                "model_name": req.api_model,
                "api_key": req.api_key,
                "http_client": _httpx.Client(headers={"User-Agent": "curl/7.88.1"}),
            }
            if req.api_base:
                kwargs["api_base"] = req.api_base
            embed = OpenAIEmbedding(**kwargs)
            result = await embed.aget_text_embedding("hello")
            return {"ok": True, "dimensions": len(result)}
        else:
            return {"ok": False, "error": "Local embedding test is not supported via API. Requires server-side model files."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/settings/ai/test/asr")
def test_asr_connection(req: TestASRRequest, user_id: str = Depends(get_current_user)):
    import requests as _requests
    try:
        resp = _requests.get(
            "https://dashscope.aliyuncs.com/api/v1/tasks",
            headers={"Authorization": f"Bearer {req.dashscope_api_key}"},
            timeout=10,
        )
        if resp.status_code == 401:
            return {"ok": False, "error": "API Key 无效"}
        return {"ok": True, "message": "API Key 验证通过"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/settings/ai/test/qiniu")
def test_qiniu_connection(req: TestQiniuRequest, user_id: str = Depends(get_current_user)):
    try:
        from qiniu import Auth as QiniuAuth
        q = QiniuAuth(req.access_key, req.secret_key)
        token = q.upload_token(req.bucket, "test-key", 60)
        if token:
            return {"ok": True, "message": "凭据验证通过"}
        return {"ok": False, "error": "无法生成上传凭证"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Multi-Channel Endpoints ──

@router.get("/settings/ai/channels")
def get_channels_config(user_id: str = Depends(get_current_user)):
    from backend.ai_config import get_channels
    from backend.channel_manager import get_health

    def mask_keys(channels: list[dict]) -> list[dict]:
        result = []
        for ch in channels:
            c = dict(ch)
            if "keys" in c:
                c["keys"] = [_mask_key(k) for k in c["keys"]]
            result.append(c)
        return result

    return {
        "llm": {"channels": mask_keys(get_channels("llm")), "health": get_health("llm")},
        "embedding": {"channels": mask_keys(get_channels("embedding")), "health": get_health("embedding")},
        "asr": {"channels": mask_keys(get_channels("asr")), "health": get_health("asr")},
    }


@router.put("/settings/ai/channels")
def update_channels_config(req: ChannelsConfig, user_id: str = Depends(get_current_user)):
    from backend.ai_config import save_channels
    from backend.llm_provider import invalidate_singletons

    emb_models = set()
    for ch in req.embedding:
        if ch.enabled and ch.backend == "api" and ch.api_model:
            emb_models.add(ch.api_model)
    if len(emb_models) > 1:
        raise HTTPException(400, f"All embedding channels must use the same model. Found: {emb_models}")

    config = {
        "llm": [ch.model_dump() for ch in req.llm],
        "embedding": [ch.model_dump() for ch in req.embedding],
        "asr": [ch.model_dump() for ch in req.asr],
    }
    save_channels(config)
    invalidate_singletons()
    return {"ok": True}


@router.post("/settings/ai/channels/test")
async def test_channel(req: TestChannelRequest, user_id: str = Depends(get_current_user)):
    ch = req.channel
    section = req.section

    proxy = ch.get("proxy", "") or None

    if section == "llm":
        try:
            from langchain_openai import ChatOpenAI
            client_kw: dict = {"headers": {"User-Agent": "curl/7.88.1"}, "follow_redirects": True}
            if proxy:
                client_kw["proxy"] = proxy
            llm = ChatOpenAI(
                model=ch.get("model", ""),
                api_key=ch.get("api_key", ""),
                base_url=ch.get("api_base", ""),
                temperature=float(ch.get("temperature", 0.7)),
                http_client=_httpx.Client(**client_kw),
                default_headers={"User-Agent": "curl/7.88.1"},
                max_tokens=20,
            )
            resp = await llm.ainvoke("Say hello in one word.")
            return {"ok": True, "message": resp.content[:100]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif section == "embedding":
        try:
            from llama_index.embeddings.openai import OpenAIEmbedding
            emb_kw: dict = {"headers": {"User-Agent": "curl/7.88.1"}}
            if proxy:
                emb_kw["proxy"] = proxy
            kwargs = {
                "model_name": ch.get("api_model", ""),
                "api_key": ch.get("api_key", ""),
                "http_client": _httpx.Client(**emb_kw),
            }
            if ch.get("api_base"):
                kwargs["api_base"] = ch["api_base"]
            embed = OpenAIEmbedding(**kwargs)
            result = await embed.aget_text_embedding("hello")
            return {"ok": True, "dimensions": len(result)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif section == "asr":
        import requests as _requests
        try:
            resp = _requests.get(
                "https://dashscope.aliyuncs.com/api/v1/tasks",
                headers={"Authorization": f"Bearer {ch.get('api_key', '')}"},
                timeout=10,
            )
            if resp.status_code == 401:
                return {"ok": False, "error": "API Key 无效"}
            return {"ok": True, "message": "API Key 验证通过"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": f"Unknown section: {section}"}


@router.get("/settings/ai/channels/health")
def get_channels_health(user_id: str = Depends(get_current_user)):
    from backend.channel_manager import get_health
    return {
        "llm": get_health("llm"),
        "embedding": get_health("embedding"),
        "asr": get_health("asr"),
    }


def _mask_key(key: str) -> str:
    if not key or len(key) <= 8:
        return "****"
    return key[:3] + "****" + key[-4:]
