"""Profile, topics, and history routes."""
import logging
import os
import re
import shutil
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from backend.config import settings
from backend.indexer import (
    index_mutation_lock, invalidate_topic_index, load_topics,
    topics_transaction,
)
from backend.utils.files import atomic_write_text
from backend.memory import get_profile, _load_profile, profile_transaction
from backend.storage.sessions import (
    get_session, list_sessions, list_sessions_by_topic,
    delete_session, list_distinct_topics, save_drill_progress,
)
from backend.storage.knowledge_cards import delete_topic_cards
from backend.auth import get_current_user
from backend.models import DrillProgressRequest

router = APIRouter(prefix="/api")
logger = logging.getLogger("uvicorn")


class TopicCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    icon: str = Field(default="", max_length=100)
    key: str = Field(default="", max_length=100)


# ── Topics ──

@router.get("/topics")
def get_topics(user_id: str = Depends(get_current_user)):
    return load_topics(user_id)


@router.post("/topics")
def create_topic(body: TopicCreateRequest, user_id: str = Depends(get_current_user)):
    if isinstance(body, BaseModel):
        raw_name, raw_icon, raw_key = body.name, body.icon, body.key
    elif isinstance(body, dict):
        # Preserve direct unit-call compatibility; FastAPI always supplies the
        # validated model above.
        raw_name = body.get("name", "")
        raw_icon = body.get("icon", "")
        raw_key = body.get("key", "")
    else:
        raise HTTPException(422, "request body must be an object")
    if not isinstance(raw_name, str) or not isinstance(raw_icon, str) or not isinstance(raw_key, str):
        raise HTTPException(422, "name, icon, and key must be strings")
    if len(raw_name) > 200 or len(raw_icon) > 100 or len(raw_key) > 100:
        raise HTTPException(422, "name, icon, or key is too long")
    name = raw_name.strip()
    icon = raw_icon.strip()
    if not name:
        raise HTTPException(400, "name is required")

    key = raw_key.strip()
    if not key:
        key = uuid.uuid4().hex[:8]
    key = re.sub(r'[^a-zA-Z0-9_-]', '', key)
    if not key:
        key = uuid.uuid4().hex[:8]

    dir_name = key
    topic_dir = settings.user_knowledge_path(user_id) / dir_name
    created_dir = False
    try:
        with topics_transaction(user_id) as topics:
            if key in topics:
                raise HTTPException(409, f"Topic '{key}' already exists")
            if not topic_dir.exists():
                topic_dir.mkdir(parents=True, exist_ok=False)
                created_dir = True
            readme = topic_dir / "README.md"
            if not readme.exists():
                atomic_write_text(readme, f"# {name}\n")
            topics[key] = {"name": name, "icon": icon, "dir": dir_name}
    except Exception:
        if created_dir and topic_dir.exists():
            shutil.rmtree(topic_dir, ignore_errors=True)
        raise

    return {"ok": True, "key": key}


@router.delete("/topics/{key}")
def delete_topic(key: str, user_id: str = Depends(get_current_user)):
    knowledge_root = settings.user_knowledge_path(user_id).resolve()
    tombstone = knowledge_root / f".topic-delete-{uuid.uuid4().hex}"
    topic_dir = None
    moved = False
    with index_mutation_lock(key, user_id):
        try:
            with topics_transaction(user_id) as topics:
                if key not in topics:
                    raise HTTPException(404, f"Topic '{key}' not found")
                topic_info = topics[key]
                topic_dir = (knowledge_root / topic_info["dir"]).resolve()
                if topic_dir == knowledge_root or knowledge_root not in topic_dir.parents:
                    raise HTTPException(400, "Invalid topic directory")
                if topic_dir.exists():
                    os.replace(topic_dir, tombstone)
                    moved = True
                # Drop persisted state while the source can still be restored
                # if invalidation or topics.json persistence fails.
                invalidate_topic_index(key, user_id, strict=True)
                delete_topic_cards(user_id=user_id, topic=key)
                del topics[key]
        except Exception:
            if (
                moved
                and topic_dir is not None
                and tombstone.exists()
                and not topic_dir.exists()
            ):
                os.replace(tombstone, topic_dir)
            raise

    if tombstone.exists():
        try:
            shutil.rmtree(tombstone)
        except OSError as exc:
            logger.warning("Unable to remove deleted topic staging dir %s: %s", tombstone, exc)
    return {"ok": True}


# ── Profile ──

@router.get("/profile")
def get_user_profile(user_id: str = Depends(get_current_user)):
    profile = get_profile(user_id)
    if not profile:
        return profile
    # Profile stores topic *keys* (e.g. the hash key of a manually-created topic).
    # Attach a key→display-name map so the frontend renders 「大厂题库」 instead of
    # `cb8f7fc8`. Shallow-copied into the response so the stored profile isn't mutated.
    names = {k: v.get("name", k) for k, v in load_topics(user_id).items()}
    return {**profile, "topic_names": names}


@router.get("/profile/export")
def export_user_profile(user_id: str = Depends(get_current_user)):
    from datetime import datetime as _dt
    profile = get_profile(user_id)
    if not profile:
        return {"markdown": "# SparkOffer 学习画像\n\n暂无训练数据。", "filename": "profile.md"}

    # key→display-name：画像里存的是 topic key，导出时映射成可读名称。
    names = {k: v.get("name", k) for k, v in load_topics(user_id).items()}

    lines = [
        "# SparkOffer 学习画像",
        f"导出时间: {_dt.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    stats = profile.get("stats", {})
    lines.append("## 训练统计")
    lines.append(f"- 总训练次数: {stats.get('total_sessions', 0)}")
    lines.append(f"- 简历面试: {stats.get('resume_sessions', 0)} 次")
    lines.append(f"- 专项训练: {stats.get('drill_sessions', 0)} 次")
    lines.append(f"- JD 备面: {stats.get('job_prep_sessions', 0)} 次")
    lines.append(f"- 综合均分: {stats.get('avg_score', '-')}")
    lines.append("")

    mastery = profile.get("topic_mastery", {})
    if mastery:
        lines.append("## 领域掌握度")
        lines.append("| 领域 | 分数 | 等级 |")
        lines.append("|------|------|------|")
        for topic_name, data in sorted(mastery.items(), key=lambda x: x[1].get("score", 0), reverse=True):
            lines.append(f"| {names.get(topic_name, topic_name)} | {data.get('score', 0)} | L{data.get('level', '?')} |")
        lines.append("")

    weak = profile.get("weak_points", [])
    if weak:
        active = [w for w in weak if not w.get("improved")]
        improved = [w for w in weak if w.get("improved")]
        if active:
            lines.append("## 当前薄弱点")
            for w in active:
                point = w.get("point", str(w)) if isinstance(w, dict) else str(w)
                topic_key = w.get("topic", "") if isinstance(w, dict) else ""
                topic_tag = f" ({names.get(topic_key, topic_key)})" if topic_key else ""
                lines.append(f"- {point}{topic_tag}")
            lines.append("")
        if improved:
            lines.append("## 已改善的薄弱点")
            for w in improved:
                point = w.get("point", str(w)) if isinstance(w, dict) else str(w)
                lines.append(f"- ~~{point}~~")
            lines.append("")

    strong = profile.get("strong_points", [])
    if strong:
        lines.append("## 强项")
        for s in strong:
            point = s.get("point", str(s)) if isinstance(s, dict) else str(s)
            lines.append(f"- {point}")
        lines.append("")

    comm = profile.get("communication", {})
    if comm.get("style") or comm.get("habits") or comm.get("suggestions"):
        lines.append("## 沟通与表达")
        if comm.get("style"):
            lines.append(f"**风格:** {comm['style']}")
        if comm.get("habits"):
            lines.append(f"**习惯:** {', '.join(comm['habits'])}")
        if comm.get("suggestions"):
            lines.append(f"**建议:** {', '.join(comm['suggestions'])}")
        lines.append("")

    tp = profile.get("thinking_patterns", {})
    if tp.get("strengths") or tp.get("gaps"):
        lines.append("## 思维模式")
        if tp.get("strengths"):
            lines.append("**优势:**")
            for s in tp["strengths"]:
                lines.append(f"- {s}")
        if tp.get("gaps"):
            lines.append("**待提升:**")
            for g in tp["gaps"]:
                lines.append(f"- {g}")
        lines.append("")

    history = stats.get("score_history", [])
    if history:
        lines.append("## 得分历史")
        lines.append("| 日期 | 模式 | 领域 | 得分 |")
        lines.append("|------|------|------|------|")
        for h in history[-20:]:
            topic_key = h.get("topic", "-")
            lines.append(f"| {h.get('date', '?')[:10]} | {h.get('mode', '?')} | {names.get(topic_key, topic_key)} | {h.get('avg_score', '?')} |")
        lines.append("")

    return {"markdown": "\n".join(lines), "filename": f"sparkoffer-profile-{_dt.now().strftime('%Y%m%d')}.md"}


@router.get("/profile/due-reviews")
def get_due_reviews_endpoint(topic: str = None, user_id: str = Depends(get_current_user)):
    from backend.spaced_repetition import get_due_reviews as _get_due
    return _get_due(user_id, topic)


@router.get("/profile/topic/{topic}/history")
def get_topic_history(topic: str, user_id: str = Depends(get_current_user)):
    return list_sessions_by_topic(topic, user_id=user_id)


@router.post("/profile/topic/{topic}/retrospective")
def generate_retrospective(topic: str, user_id: str = Depends(get_current_user)):
    from backend.prompts.interviewer import TOPIC_RETROSPECTIVE_PROMPT
    from langchain_core.messages import SystemMessage, HumanMessage

    sessions = list_sessions_by_topic(topic, user_id=user_id)
    if not sessions:
        raise HTTPException(400, "该领域暂无训练记录")

    profile = _load_profile(user_id)
    topic_display = {k: v["name"] for k, v in load_topics(user_id).items()}
    topic_name = topic_display.get(topic, topic)
    mastery = profile.get("topic_mastery", {}).get(topic, {})

    # Short-circuit if the cached retrospective is already up-to-date (no newer sessions).
    retrospective_at = mastery.get("retrospective_at")
    if retrospective_at:
        def _parse_dt(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None

        retrospective_dt = _parse_dt(retrospective_at)
        session_update_times = [
            dt for dt in (_parse_dt(s.get("updated_at") or s.get("created_at")) for s in sessions)
            if dt is not None
        ]
        newest_session_at = max(session_update_times, default=None)
        if retrospective_dt and newest_session_at and newest_session_at <= retrospective_dt:
            # No new data since last retrospective: return cached result immediately.
            from backend.utils.sse_helpers import streaming_response, sse_event
            cached_retrospective = mastery.get("retrospective", "")
            async def _cached():
                yield sse_event({"type": "complete", "data": {
                    "topic": topic, "topic_name": topic_name,
                    "retrospective": cached_retrospective, "session_count": len(sessions),
                }})
                yield sse_event({"type": "done"})
            return streaming_response(_cached())

    history_lines = []
    for s in sessions:
        date = s["created_at"][:10]
        scores = s.get("scores", [])
        valid_scores = [sc for sc in scores if isinstance(sc.get("score"), (int, float))]
        avg = round(sum(sc["score"] for sc in valid_scores) / len(valid_scores), 1) if valid_scores else None

        review = s.get("review") or ""
        summary_part = review.split("## 逐题复盘")[0].strip()

        score_lines = []
        for sc in valid_scores:
            line = f"- Q{sc.get('question_id', '?')}: {sc['score']}/10"
            if sc.get("assessment"):
                line += f" — {sc['assessment']}"
            score_lines.append(line)

        history_lines.append(
            f"### {date} (答题 {len(valid_scores)}/10, 平均 "
            f"{avg if avg is not None else '无'}/10)\n"
            f"{summary_part}\n"
            + ("\n".join(score_lines) + "\n" if score_lines else "")
        )

    mastery_score = mastery.get("score", mastery.get("level", 0) * 20)
    mastery_text = f"{mastery_score}/100 — {mastery.get('notes', '')}" if mastery_score > 0 else "暂无评估"

    prompt = TOPIC_RETROSPECTIVE_PROMPT.format(
        topic_name=topic_name,
        session_history="\n".join(history_lines),
        mastery_info=mastery_text,
    )

    from backend.utils.sse_helpers import stream_llm_sse, streaming_response, sse_event

    lc_messages = [
        SystemMessage(content="你是面试教练。用 markdown 生成回顾报告。"),
        HumanMessage(content=prompt),
    ]
    session_count = len(sessions)

    async def _gen():
        retrospective = ""
        async for kind, value in stream_llm_sse(lc_messages, progress_prefix="正在生成回顾报告"):
            if kind == "sse":
                yield value
            elif kind == "error":
                return  # LLM failed — keep the previously cached retrospective
            else:
                retrospective = value.strip()

        if not retrospective:
            yield sse_event({"type": "error", "message": "生成内容为空，请稍后重试。"})
            return

        # Re-read under the lock: the pre-stream `profile` snapshot is minutes
        # old by now and saving it would clobber any updates made meanwhile.
        with profile_transaction(user_id) as fresh:
            fresh.setdefault("topic_mastery", {}).setdefault(topic, {})["retrospective"] = retrospective
            fresh["topic_mastery"][topic]["retrospective_at"] = datetime.now().isoformat()

        yield sse_event({"type": "complete", "data": {
            "topic": topic, "topic_name": topic_name,
            "retrospective": retrospective, "session_count": session_count,
        }})
        yield sse_event({"type": "done"})

    return streaming_response(_gen())


# ── History ──

@router.get("/interview/review/{session_id}")
def get_review(session_id: str, user_id: str = Depends(get_current_user)):
    session = get_session(session_id, user_id=user_id)
    if not session:
        raise HTTPException(404, "Session not found.")
    if not session.get("review"):
        raise HTTPException(400, "Interview not yet reviewed.")
    return session


@router.get("/interview/history")
def get_history(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    mode: str | None = Query(default=None, max_length=100),
    topic: str | None = Query(default=None, max_length=200),
    status: str = Query(default="completed", pattern="^(completed|in_progress|all)$"),
    user_id: str = Depends(get_current_user),
):
    result = list_sessions(
        user_id=user_id, limit=limit, offset=offset,
        mode=mode, topic=topic, status=status,
    )
    # Sessions persist the topic *key* — a hash like `cb8f7fc8` for a manually
    # created module. Attach the display name so 时光机 renders 「大厂题库」 instead
    # of the hash. Mirrors get_user_profile()'s topic_names handling; `topic`
    # stays the key for any key-based consumer.
    names = {k: v.get("name", k) for k, v in load_topics(user_id).items()}
    for item in result.get("items", []):
        key = item.get("topic")
        if key:
            item["topic_name"] = names.get(key, key)
    return result


@router.get("/interview/session/{session_id}")
async def get_interview_session(session_id: str, user_id: str = Depends(get_current_user)):
    """Full session state for resuming an in-progress drill / job_prep."""
    import asyncio
    session = await asyncio.to_thread(get_session, session_id, user_id=user_id)
    if not session:
        raise HTTPException(404, "Session not found.")
    # Completed sessions (review written) are read-only history: do NOT
    # re-populate live_store, or /interview/end could re-evaluate them and
    # overwrite the original review + double-count profile/SR stats.
    if session.get("review"):
        return session
    # Re-populate live_store if the backend has been restarted since session start
    from backend.live_store import drill_sessions, job_prep_sessions, save_live
    mode = session.get("mode")
    if mode == "resume":
        meta = session.get("meta", {}) or {}
        if "resume_phase" in meta and "resume_is_finished" in meta:
            session["resume_state"] = {
                "phase": meta.get("resume_phase"),
                "is_finished": bool(meta.get("resume_is_finished")),
            }
        else:
            try:
                from backend.graphs.resume_interview import compile_resume_interview

                def _load_resume_state():
                    graph = compile_resume_interview(user_id)
                    snapshot = graph.get_state({
                        "configurable": {"thread_id": session_id},
                    })
                    values = getattr(snapshot, "values", None) or {}
                    if not values.get("messages") and values.get("phase") is None:
                        # The sessions row survived but its checkpoint is gone
                        # (deleted checkpoints.db / cleaned thread). Chatting
                        # against an empty checkpoint would re-run init from
                        # START — surface it as unrecoverable instead.
                        return {"phase": None, "is_finished": False,
                                "recoverable": False}
                    return {
                        "phase": values.get("phase"),
                        "is_finished": bool(values.get("is_finished")),
                        "recoverable": True,
                    }

                session["resume_state"] = await asyncio.to_thread(_load_resume_state)
            except Exception as exc:
                logger.warning(
                    "Could not restore resume graph state for %s: %s",
                    session_id,
                    exc,
                )
                raise HTTPException(
                    503,
                    "Resume interview state is temporarily unavailable.",
                ) from exc
    if mode == "topic_drill" and session_id not in drill_sessions:
        save_live(drill_sessions, session_id, "drill", user_id, {
            "topic": session.get("topic"),
            "questions": session.get("questions", []),
            "user_id": user_id,
        })
    elif mode == "jd_prep" and session_id not in job_prep_sessions:
        meta = session.get("meta", {}) or {}
        save_live(job_prep_sessions, session_id, "job_prep", user_id, {
            "questions": session.get("questions", []),
            "preview": meta.get("preview", {}),
            "meta": meta,
            "user_id": user_id,
        })
    return session


@router.post("/interview/session/{session_id}/progress")
async def save_session_progress(
    session_id: str, body: DrillProgressRequest,
    user_id: str = Depends(get_current_user),
):
    """Persist mid-drill progress (current_index, partial_answers, hints) to meta.progress."""
    import asyncio
    ok = await asyncio.to_thread(
        save_drill_progress,
        session_id,
        body.current_index,
        body.partial_answers,
        body.hints,
        user_id=user_id,
    )
    if not ok:
        raise HTTPException(404, "Session not found.")
    return {"ok": True}


@router.delete("/interview/session/{session_id}")
def delete_session_endpoint(session_id: str, user_id: str = Depends(get_current_user)):
    deleted = delete_session(session_id, user_id=user_id)
    if not deleted:
        raise HTTPException(404, "Session not found.")
    # Evict any in-memory live copy so the deleted session can't be continued
    # from the process cache (the SQLite live_sessions row is already gone).
    from backend.live_store import algorithm_sessions, drill_sessions, graphs, job_prep_sessions
    for store in (graphs, drill_sessions, job_prep_sessions, algorithm_sessions):
        store.pop(session_id, None)
    return {"ok": True}


@router.get("/interview/topics")
def get_interview_topics(user_id: str = Depends(get_current_user)):
    # Return {key, name} so the 时光机 filter shows 「大厂题库」 while still filtering
    # by the stored topic key (a hash for manually-created modules).
    keys = list_distinct_topics(user_id=user_id)
    names = {k: v.get("name", k) for k, v in load_topics(user_id).items()}
    return [{"key": k, "name": names.get(k, k)} for k in keys]
