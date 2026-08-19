"""FastAPI entry point — SparkOffer interview training system."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.utils.request_limits import RequestBodyLimitMiddleware
from backend.routers import (
    auth, settings_router, resume, interview,
    profile, knowledge, job_prep, algorithm, favorites,
    assistant, graph_router, qa_arena, rag_eval, debug,
    knowledge_training,
)

logger = logging.getLogger("uvicorn")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    security_issues = settings.validate_security_settings()
    for issue in security_issues:
        logger.warning("Insecure development setting: %s", issue)

    from backend.llm_provider import get_embedding
    from backend.indexer import _init_llama_settings
    from backend.storage.database import init_all_tables
    from backend.ai_config import init_config, get_effective
    from backend.storage.live_sessions import cleanup_expired_sessions
    from backend.auth import ensure_default_user

    init_config()
    init_all_tables()

    emb_backend = get_effective("embedding", "backend") or settings.embedding_backend_mode()
    emb_model = get_effective("embedding", "api_model") or settings.active_embedding_target()
    logger.info("Initializing embedding backend=%s target=%s", emb_backend, emb_model)
    get_embedding()
    _init_llama_settings()
    logger.info("Embedding backend ready.")

    cleanup_expired_sessions()
    ensure_default_user()

    from backend.redis_cache import init_cache
    cache = init_cache(settings.redis_url)
    logger.info("Cache backend: %s", cache.health()["backend"])

    from backend.channel_manager import get_all_channels
    for sec in ("llm", "embedding"):
        chs = get_all_channels(sec)
        if chs:
            enabled = sum(1 for c in chs if c.get("enabled", True))
            logger.info("Multi-channel %s: %d channels (%d enabled)", sec.upper(), len(chs), enabled)

    # Start background embedding task queue
    from backend.embedding_tasks import get_task_queue
    await get_task_queue().start()

    # Pre-warm knowledge indices in the background. The first retrieval per topic
    # otherwise pays a 12-27s cold start (synchronous index load / full rebuild +
    # re-embedding). Fire-and-forget so startup isn't blocked; lazy-load still
    # covers any topic that hasn't finished warming. Keep a reference so the task
    # isn't garbage-collected mid-flight.
    import asyncio
    from backend.indexer import warmup_user_indices
    app.state.warmup_task = asyncio.create_task(warmup_user_indices())

    logger.info("Startup complete.")

    yield

    # Graceful shutdown: cancel warmup if still running, then stop the task queue
    warmup_task = getattr(app.state, "warmup_task", None)
    if warmup_task is not None:
        if not warmup_task.done():
            warmup_task.cancel()
        try:
            await warmup_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Knowledge-index warmup ended with an error: %s", exc)

    from backend.embedding_tasks import get_task_queue as _get_tq
    await _get_tq().stop()


app = FastAPI(title="SparkOffer", version="0.3.0", lifespan=lifespan)


def _request_limit_for_path(path: str) -> int | None:
    """Return the larger streaming limit only for multipart upload routes."""
    normalized = path.rstrip("/")
    if normalized.startswith("/api/knowledge/") and normalized.endswith("/upload"):
        return settings.max_knowledge_upload_request_bytes
    if normalized == "/api/resume/upload":
        return settings.max_resume_upload_request_bytes
    return None


@app.get("/health/live", include_in_schema=False)
def health_live():
    """Process liveness probe: no dependency calls and safe during startup."""
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
def health_ready():
    """Readiness probe covering the database and configured vector backend."""
    from fastapi import HTTPException
    from backend.storage.database import get_db

    try:
        get_db().execute("SELECT 1").fetchone()
        if settings.vector_backend_mode() == "qdrant":
            from backend.indexer import _qdrant_kb_available
            if not _qdrant_kb_available():
                raise RuntimeError("qdrant is not ready")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="dependencies not ready") from exc
    return {"status": "ok", "vector_backend": settings.vector_backend_mode()}

app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=settings.max_request_body_bytes,
    path_limit_resolver=_request_limit_for_path,
)
_cors_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(settings_router.router)
app.include_router(resume.router)
app.include_router(interview.router)
app.include_router(profile.router)
app.include_router(knowledge.router)
app.include_router(knowledge_training.router)
app.include_router(job_prep.router)
app.include_router(algorithm.router)
app.include_router(favorites.router)
app.include_router(assistant.router)
app.include_router(graph_router.router)
app.include_router(qa_arena.router)
app.include_router(rag_eval.router)
app.include_router(debug.router)
