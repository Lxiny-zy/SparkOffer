"""FastAPI entry point — SparkOffer interview training system."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.routers import (
    auth, settings_router, resume, interview, recording,
    profile, knowledge, job_prep, algorithm, favorites,
    assistant, graph_router, qa_arena, rag_eval, debug,
    knowledge_training,
)

logger = logging.getLogger("uvicorn")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    from backend.llm_provider import get_embedding
    from backend.indexer import _init_llama_settings
    from backend.storage.database import init_all_tables
    from backend.ai_config import init_config, get_effective
    from backend.storage.live_sessions import cleanup_expired_sessions
    from backend.auth import ensure_default_user
    from backend.config import settings

    init_config()
    init_all_tables()

    # Security check: warn if JWT secret is the default value
    if settings.jwt_secret == "change-me-in-production":
        logger.warning(
            "⚠️  JWT_SECRET is using the default value! "
            "Set JWT_SECRET in .env for production deployments. "
            "Anyone can forge authentication tokens with the default secret."
        )

    # Security check: warn if the default account password is unchanged
    if settings.default_password == "legend":
        logger.warning(
            "⚠️  DEFAULT_PASSWORD is using the default value 'legend'! "
            "The default account (%s) is loginable — set DEFAULT_PASSWORD in .env "
            "for any non-local deployment.",
            settings.default_email,
        )

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

    from backend.channel_manager import has_channels, get_all_channels
    for sec in ("llm", "embedding", "asr"):
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
    if warmup_task is not None and not warmup_task.done():
        warmup_task.cancel()

    from backend.embedding_tasks import get_task_queue as _get_tq
    await _get_tq().stop()


app = FastAPI(title="SparkOffer", version="0.3.0", lifespan=lifespan)

_cors_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()] or ["*"]
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
app.include_router(recording.router)
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
