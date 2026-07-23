"""Personalized strategy — invokes the real DrillPipeline against a synthetic user.

We materialize the persona JSON into a temporary user directory under
``data/users/<eval_user_id>/profile/profile.json`` (using ``settings.user_profile_dir``)
plus a minimal ``topics.json`` so the pipeline can resolve topic display names.

The pipeline yields SSE strings; we parse out ``type=="question"`` events and
collect them.

NOTE: this strategy depends on:
  - At least one ``llm`` channel being configured (real LLM call).
  - Either the user knowledge dir existing OR ``safe_retrieve_topic_context``
    returning empty list gracefully — which it does (see indexer.py:259).
If no LLM channel is configured the call raises at the ``generate`` stage and
we propagate the exception so the runner can skip and log.
"""
from __future__ import annotations

import json
import logging
import shutil
import uuid

from backend.config import settings
from backend.eval.strategies.base import Strategy

logger = logging.getLogger("uvicorn")


_EVAL_USER_PREFIX = "eval_"


def _materialize_persona(persona: dict, topic: str) -> str:
    """Write persona to a fresh eval user dir; return user_id.

    Each call gets a unique user_id so concurrent eval runs don't clobber
    each other. Caller is responsible for cleanup via ``_cleanup_persona``.
    """
    user_id = f"{_EVAL_USER_PREFIX}{persona.get('persona_id', 'anon')}_{uuid.uuid4().hex[:6]}"
    profile_dir = settings.user_profile_dir(user_id)
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Strip eval-only metadata before saving.
    persona_copy = {k: v for k, v in persona.items() if k != "persona_id"}
    profile_path = profile_dir / "profile.json"
    profile_path.write_text(
        json.dumps(persona_copy, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # topics.json — point the eval user at the SAME topic dirs the real user has
    # so RAG retrieval can find chunks. We mirror data/topics.example.json or
    # the running user's topics.json if available; otherwise inline a minimal map.
    topics_path = settings.user_topics_path(user_id)
    topics_path.parent.mkdir(parents=True, exist_ok=True)
    default_topics = {
        "python": {"name": "Python 核心", "icon": "Terminal", "dir": "python"},
        "java": {"name": "Java 后端", "icon": "Code", "dir": "java"},
        "agent": {"name": "AI Agent 工程", "icon": "Workflow", "dir": "agent"},
    }
    # Restrict topics.json to the topic we're testing so the indexer doesn't
    # try to build indices for topics whose knowledge dir is missing.
    if topic in default_topics:
        topics_path.write_text(
            json.dumps({topic: default_topics[topic]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        topics_path.write_text(
            json.dumps(default_topics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # Symlink knowledge dir from the global data dir if it exists so RAG works
    # without copying. Symlinks on Windows need either admin or developer-mode;
    # if creation fails we skip (RAG just returns [] — pipeline still works,
    # just with empty knowledge_ctx). We avoid copytree because the knowledge
    # dir can be 50MB+ and we don't want eval to be slow on every cell.
    global_knowledge = settings.knowledge_path / default_topics.get(topic, {}).get("dir", topic)
    user_knowledge = settings.user_knowledge_path(user_id)
    user_knowledge.mkdir(parents=True, exist_ok=True)
    if global_knowledge.exists():
        target_dir = user_knowledge / default_topics.get(topic, {}).get("dir", topic)
        try:
            if not target_dir.exists():
                target_dir.symlink_to(global_knowledge, target_is_directory=True)
        except (OSError, NotImplementedError) as e:
            logger.info("Symlink failed for eval user %s (%s); RAG will be skipped for this run", user_id, e)

    return user_id


def _cleanup_persona(user_id: str):
    """Remove the materialized eval user dir. Best-effort."""
    if not user_id.startswith(_EVAL_USER_PREFIX):
        logger.warning("Refusing to cleanup non-eval user_id: %s", user_id)
        return
    user_dir = settings.user_data_dir(user_id)
    if user_dir.exists():
        try:
            shutil.rmtree(user_dir, ignore_errors=True)
        except Exception as e:
            logger.warning("Cleanup failed for %s: %s", user_dir, e)


class PersonalizedStrategy(Strategy):
    name = "personalized"

    async def generate_questions(
        self, persona: dict, topic: str, n_questions: int = 10,
    ) -> list[dict]:
        # Import inside the method so the module is importable even when the
        # backend is not fully configured (e.g. running --help).
        from backend.graphs.drill_pipeline import DrillPipeline

        user_id = _materialize_persona(persona, topic)
        try:
            questions: list[dict] = []
            pipeline = DrillPipeline(topic=topic, user_id=user_id)
            async for raw_event in pipeline.run():
                # raw_event format: "data: {json}\n\n"
                if not raw_event.startswith("data: "):
                    continue
                try:
                    payload = json.loads(raw_event[len("data: "):].rstrip())
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "question":
                    q = payload.get("data") or {}
                    if q:
                        questions.append(q)
                elif payload.get("type") == "error":
                    logger.warning("Pipeline error during eval: %s", payload.get("message"))
            return questions[:n_questions]
        finally:
            _cleanup_persona(user_id)
