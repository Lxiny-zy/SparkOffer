"""Profile, topics, and history routes."""
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends

from backend.config import settings
from backend.indexer import load_topics, save_topics, _index_cache
from backend.memory import get_profile, _load_profile, profile_transaction
from backend.storage.sessions import (
    get_session, list_sessions, list_sessions_by_topic,
    delete_session, list_distinct_topics, save_drill_progress,
)
from backend.auth import get_current_user

router = APIRouter(prefix="/api")


# ── Topics ──

@router.get("/topics")
def get_topics(user_id: str = Depends(get_current_user)):
    return load_topics(user_id)


@router.post("/topics")
def create_topic(body: dict, user_id: str = Depends(get_current_user)):
    name = body.get("name", "").strip()
    icon = body.get("icon", "").strip()
    if not name:
        raise HTTPException(400, "name is required")

    key = body.get("key", "").strip()
    if not key:
        key = uuid.uuid4().hex[:8]
    key = re.sub(r'[^a-zA-Z0-9_-]', '', key)
    if not key:
        key = uuid.uuid4().hex[:8]

    topics = load_topics(user_id)
    if key in topics:
        raise HTTPException(409, f"Topic '{key}' already exists")

    dir_name = key
    topics[key] = {"name": name, "icon": icon, "dir": dir_name}
    save_topics(topics, user_id)

    topic_dir = settings.user_knowledge_path(user_id) / dir_name
    topic_dir.mkdir(parents=True, exist_ok=True)
    readme = topic_dir / "README.md"
    if not readme.exists():
        readme.write_text(f"# {name}\n", encoding="utf-8")

    return {"ok": True, "key": key}


@router.delete("/topics/{key}")
def delete_topic(key: str, user_id: str = Depends(get_current_user)):
    topics = load_topics(user_id)
    if key not in topics:
        raise HTTPException(404, f"Topic '{key}' not found")
    del topics[key]
    save_topics(topics, user_id)
    _index_cache.pop((user_id, key), None)
    return {"ok": True}


# ── Profile ──

@router.get("/profile")
def get_user_profile(user_id: str = Depends(get_current_user)):
    return get_profile(user_id)


@router.get("/profile/export")
def export_user_profile(user_id: str = Depends(get_current_user)):
    from datetime import datetime as _dt
    profile = get_profile(user_id)
    if not profile:
        return {"markdown": "# SparkOffer 学习画像\n\n暂无训练数据。", "filename": "profile.md"}

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
            lines.append(f"| {topic_name} | {data.get('score', 0)} | L{data.get('level', '?')} |")
        lines.append("")

    weak = profile.get("weak_points", [])
    if weak:
        active = [w for w in weak if not w.get("improved")]
        improved = [w for w in weak if w.get("improved")]
        if active:
            lines.append("## 当前薄弱点")
            for w in active:
                point = w.get("point", str(w)) if isinstance(w, dict) else str(w)
                topic_tag = f" ({w.get('topic', '')})" if isinstance(w, dict) and w.get("topic") else ""
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
            lines.append(f"| {h.get('date', '?')[:10]} | {h.get('mode', '?')} | {h.get('topic', '-')} | {h.get('avg_score', '?')} |")
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
    from backend.llm_provider import get_langchain_llm
    from langchain_core.messages import SystemMessage, HumanMessage

    sessions = list_sessions_by_topic(topic, user_id=user_id)
    if not sessions:
        raise HTTPException(400, "该领域暂无训练记录")

    profile = _load_profile(user_id)
    topic_display = {k: v["name"] for k, v in load_topics(user_id).items()}
    topic_name = topic_display.get(topic, topic)
    mastery = profile.get("topic_mastery", {}).get(topic, {})

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
            f"### {date} (答题 {len(valid_scores)}/10, 平均 {avg or '无'}/10)\n"
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
            else:
                retrospective = value.strip()

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
    limit: int = 20, offset: int = 0,
    mode: str = None, topic: str = None,
    status: str = "completed",
    user_id: str = Depends(get_current_user),
):
    return list_sessions(
        user_id=user_id, limit=limit, offset=offset,
        mode=mode, topic=topic, status=status,
    )


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
    session_id: str, body: dict,
    user_id: str = Depends(get_current_user),
):
    """Persist mid-drill progress (current_index, partial_answers, hints) to meta.progress."""
    import asyncio
    ok = await asyncio.to_thread(
        save_drill_progress,
        session_id,
        int(body.get("current_index", 0)),
        body.get("partial_answers", {}) or {},
        body.get("hints", {}) or {},
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
    return {"ok": True}


@router.get("/interview/topics")
def get_interview_topics(user_id: str = Depends(get_current_user)):
    return list_distinct_topics(user_id=user_id)
