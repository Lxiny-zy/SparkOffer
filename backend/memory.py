"""个性化记忆系统 — 跨面试用户画像。

设计哲学：
- 文件即真相（OpenClaw）：profile.json 可人工编辑
- 两阶段提取（Mem0）：Extract → Update，不无脑追加
- 向量召回（embedding）：语义搜索历史洞察
"""
import asyncio
import copy
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from langchain_core.messages import SystemMessage, HumanMessage

from backend.config import settings
from backend.llm_provider import get_langchain_llm
from backend.utils.files import (
    atomic_write_text,
    exclusive_file_lock,
    file_mutation_lock_path,
)

logger = logging.getLogger("uvicorn")

# Per-user file locks to prevent concurrent read-modify-write races on profile.json
_profile_locks: dict[str, threading.Lock] = {}
_profile_locks_lock = threading.Lock()

# Operation records live in the profile so a session retry can fence a
# side-effect even after the session-level marker write was interrupted.
# Keep the map bounded: it is a recovery journal, not an unbounded audit log.
_PROFILE_OPERATIONS_KEY = "_applied_operations"
_PROFILE_OPERATIONS_LIMIT = 512
_PROFILE_OPERATION_PRUNE_GRACE = timedelta(days=1)


def _get_profile_lock(user_id: str) -> threading.Lock:
    """Get or create a per-user lock for profile file operations."""
    with _profile_locks_lock:
        if user_id not in _profile_locks:
            _profile_locks[user_id] = threading.Lock()
        return _profile_locks[user_id]

# ── Profile Schema ──

DEFAULT_PROFILE = {
    "name": "",
    "target_role": "通用 Agent 工程师（Python 或 Java 后端方向）",
    "updated_at": "",

    # 技术掌握度 (topic → {level: 1-5, notes: str})
    "topic_mastery": {},

    # 薄弱点 (list of {point, topic, first_seen, last_seen, times_seen, improved})
    "weak_points": [],

    # 强项 (list of {point, topic, first_seen})
    "strong_points": [],

    # 表达与沟通特征
    "communication": {
        "style": "",        # e.g. "回答偏短，缺少具体例子"
        "habits": [],       # e.g. ["紧张时语速加快", "喜欢用类比解释"]
        "suggestions": [],  # e.g. ["多用 STAR 法描述项目"]
    },

    # 答题思维模式
    "thinking_patterns": {
        "strengths": [],    # e.g. ["能用类比解释抽象概念", "项目描述有数据支撑"]
        "gaps": [],         # e.g. ["对比类问题缺乏结构", "被追问 why 时容易卡住"]
    },

    # 用户偏好（助手交互风格）
    "preferences": {
        "response_style": "",       # "简洁" / "详细" / ""
        "preferred_difficulty": "",  # "简单" / "中等" / "困难" / ""
        "focus_topics": [],         # 用户主动要求关注的主题
        "interview_pace": "",       # "快" / "慢" / ""
        "feedback_style": "",       # "直接" / "鼓励型" / ""
        "custom_notes": [],         # 自由格式的用户偏好备注
    },

    # 面试统计
    "stats": {
        "total_sessions": 0,
        "resume_sessions": 0,
        "drill_sessions": 0,
        "job_prep_sessions": 0,
        "avg_score": 0,
        "score_history": [],  # [{date, mode, topic, avg_score}]
    },
}

from backend.prompts.memory import EXTRACT_PROMPT  # noqa: E402  (提取 prompt 已集中到 prompts/)


# ── Per-user path helpers ──

def _profile_path(user_id: str) -> Path:
    return settings.user_profile_dir(user_id) / "profile.json"


def _insights_dir(user_id: str) -> Path:
    return settings.user_profile_dir(user_id) / "insights"


def _load_profile_unlocked(user_id: str) -> dict:
    path = _profile_path(user_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    # deepcopy, not .copy(): a shallow copy shares DEFAULT_PROFILE's nested
    # lists/dicts (weak_points, strong_points, topic_mastery, ...). Mutating a
    # new user's profile would then leak into the global default and bleed
    # into the next new user's "empty" profile.
    return copy.deepcopy(DEFAULT_PROFILE)


def _load_profile(user_id: str) -> dict:
    """Load user profile with file lock protection.

    NOTE: this only protects the single read. Any read-modify-write sequence
    must go through profile_transaction() instead — load + mutate + save with
    separate lock acquisitions loses concurrent updates (last writer wins).
    """
    lock = _get_profile_lock(user_id)
    with lock:
        return _load_profile_unlocked(user_id)


def _save_profile_unlocked(profile: dict, user_id: str):
    path = _profile_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile["updated_at"] = datetime.now().isoformat()
    data = json.dumps(profile, ensure_ascii=False, indent=2)

    # Atomic write: write to temp file in same directory, then rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=".profile_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_path, str(path))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _save_profile(profile: dict, user_id: str):
    """Save user profile atomically with file lock protection.

    Uses write-to-temp-then-rename pattern to prevent corruption
    if the process crashes mid-write. See _load_profile's note: prefer
    profile_transaction() for read-modify-write sequences.
    """
    lock = _get_profile_lock(user_id)
    with lock:
        with exclusive_file_lock(file_mutation_lock_path(_profile_path(user_id))):
            _save_profile_unlocked(profile, user_id)


class ProfileTransactionAbort(Exception):
    """Raise inside a profile_transaction block to exit without saving."""


@contextmanager
def profile_transaction(user_id: str):
    """Hold the per-user lock across an entire read-modify-write.

    Yields the freshly loaded profile and saves it on normal exit, so two
    concurrent updaters can't both load v0 and overwrite each other's writes.
    The body must be synchronous and fast (no awaits, no LLM/embedding calls)
    — resolve anything slow against a snapshot BEFORE entering, then re-apply
    to the fresh profile inside.
    """
    lock = _get_profile_lock(user_id)
    with lock:
        with exclusive_file_lock(file_mutation_lock_path(_profile_path(user_id))):
            profile = _load_profile_unlocked(user_id)
            try:
                yield profile
            except ProfileTransactionAbort:
                return
            _save_profile_unlocked(profile, user_id)


def _get_applied_operation(profile: dict, operation_id: str | None) -> dict | None:
    """Return one durable operation record, if it has already been applied."""
    if not operation_id:
        return None
    operations = profile.get(_PROFILE_OPERATIONS_KEY)
    if not isinstance(operations, dict):
        return None
    record = operations.get(operation_id)
    return record if isinstance(record, dict) else None


def _record_applied_operation(
    profile: dict,
    operation_id: str | None,
    *,
    result: dict | None = None,
) -> None:
    """Record a completed operation while the profile transaction is locked."""
    if not operation_id:
        return
    operations = profile.setdefault(_PROFILE_OPERATIONS_KEY, {})
    if not isinstance(operations, dict):
        operations = {}
        profile[_PROFILE_OPERATIONS_KEY] = operations
    # Re-inserting moves a retried record to the newest end of the bounded map.
    operations.pop(operation_id, None)
    record = {"applied_at": datetime.now().isoformat()}
    if result is not None:
        record["result"] = copy.deepcopy(result)
    operations[operation_id] = record
    _prune_confirmed_operations(operations)


def _prune_confirmed_operations(operations: dict) -> None:
    """Bound old confirmed records without ever evicting recovery markers."""
    if len(operations) <= _PROFILE_OPERATIONS_LIMIT:
        return
    cutoff = datetime.now() - _PROFILE_OPERATION_PRUNE_GRACE
    for operation_id, record in list(operations.items()):
        if len(operations) <= _PROFILE_OPERATIONS_LIMIT:
            break
        if not isinstance(record, dict):
            continue
        confirmed_at = record.get("confirmed_at")
        if not isinstance(confirmed_at, str):
            continue
        try:
            confirmed_time = datetime.fromisoformat(confirmed_at)
        except ValueError:
            continue
        if confirmed_time <= cutoff:
            operations.pop(operation_id, None)


def confirm_profile_session_operations(user_id: str, session_id: str) -> int:
    """Mark target records GC-eligible only after the session is fully synced."""
    prefix = f"session-sync:{session_id}:"
    confirmed = 0
    now = datetime.now().isoformat()
    with profile_transaction(user_id) as profile:
        operations = profile.get(_PROFILE_OPERATIONS_KEY)
        if not isinstance(operations, dict):
            raise ProfileTransactionAbort
        for operation_id, record in operations.items():
            if operation_id.startswith(prefix) and isinstance(record, dict):
                if "confirmed_at" not in record:
                    record["confirmed_at"] = now
                    confirmed += 1
        before_prune = len(operations)
        _prune_confirmed_operations(operations)
        if not confirmed and len(operations) == before_prune:
            raise ProfileTransactionAbort
    return confirmed


def _profile_operation_result(user_id: str, operation_id: str | None) -> dict | None:
    """Read a stored operation result without exposing the journal publicly."""
    if not operation_id:
        return None
    record = _get_applied_operation(_load_profile(user_id), operation_id)
    if not record or "result" not in record:
        return None
    result = record.get("result")
    # A legacy malformed result still belongs to the journal winner. Treat it
    # as an empty extraction instead of falling back to a race loser's payload.
    return copy.deepcopy(result) if isinstance(result, dict) else {}


def _finite_number(value):
    """Return a JSON-safe finite number, or ``None`` for malformed input."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return value if math.isfinite(value) else None
        except OverflowError:
            return None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
    return None


def _clean_text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_string_list(value) -> list[str]:
    """Normalize a string list while retaining useful scalar LLM output."""
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return [item.strip() for item in values if isinstance(item, str) and item.strip()]


def _normalize_points(value, topic: str | None = None) -> list:
    """Keep valid point fields and discard malformed JSON elements."""
    if isinstance(value, (str, dict)):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        return []

    normalized = []
    for item in values:
        if isinstance(item, str):
            point = _clean_text(item)
            if point:
                normalized.append(point)
            continue
        if not isinstance(item, dict):
            continue
        point = _clean_text(item.get("point"))
        if not point:
            continue
        clean = copy.deepcopy(item)
        clean["point"] = point
        if "topic" in clean:
            clean_topic = _clean_text(clean.get("topic"))
            if clean_topic:
                clean["topic"] = clean_topic
            elif topic:
                clean["topic"] = topic
            else:
                clean.pop("topic", None)
        normalized.append(clean)
    return normalized


def _normalize_mastery(value) -> dict:
    if not isinstance(value, dict):
        return {}

    def clean_entry(entry):
        if not isinstance(entry, dict):
            return None
        clean = copy.deepcopy(entry)
        for key in ("score", "level"):
            if key in clean:
                number = _finite_number(clean[key])
                if number is None:
                    clean.pop(key, None)
                else:
                    clean[key] = number
        if "notes" in clean:
            notes = _clean_text(clean["notes"])
            if notes:
                clean["notes"] = notes
            else:
                clean.pop("notes", None)
        return clean

    # Backward-compatible single-topic shape: {score, notes}.
    if any(key in value for key in ("score", "level", "notes")):
        entry = clean_entry(value)
        return entry or {}

    result = {}
    for key, entry in value.items():
        if not isinstance(key, str) or not key.strip():
            continue
        clean = clean_entry(entry)
        if clean is not None:
            result[key] = clean
    return result


def _normalize_observations(value, *, fields: tuple[str, ...]) -> dict:
    if not isinstance(value, dict):
        return {}
    result = copy.deepcopy(value)
    for field in fields:
        if field not in result:
            continue
        if field.endswith("_update") or field == "style_update":
            text = _clean_text(result[field])
            if text:
                result[field] = text
            else:
                result.pop(field, None)
        else:
            result[field] = _clean_string_list(result[field])
    return result


def _normalize_dimension_scores(value) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, score in value.items():
        if not isinstance(key, str) or not key.strip():
            continue
        number = _finite_number(score)
        if number is not None:
            result[key] = number
    return result


def _normalize_extraction(value, topic: str | None = None) -> dict:
    """Sanitize extraction fields before profile markers or artifact writes."""
    source = value if isinstance(value, dict) else {}
    result = copy.deepcopy(source)
    if "session_summary" in source:
        result["session_summary"] = _clean_text(source.get("session_summary"))
    if "weak_points" in source:
        result["weak_points"] = _normalize_points(source.get("weak_points"), topic)
    if "strong_points" in source:
        result["strong_points"] = _normalize_points(source.get("strong_points"), topic)
    if "topic_mastery" in source:
        result["topic_mastery"] = _normalize_mastery(source.get("topic_mastery"))
    if "communication_observations" in source:
        result["communication_observations"] = _normalize_observations(
            source.get("communication_observations"),
            fields=("style_update", "new_habits", "new_suggestions"),
        )
    if "communication" in source:
        result["communication"] = _normalize_observations(
            source.get("communication"),
            fields=("style_update", "new_habits", "new_suggestions"),
        )
    if "thinking_patterns" in source:
        result["thinking_patterns"] = _normalize_observations(
            source.get("thinking_patterns"),
            fields=("new_strengths", "new_gaps"),
        )
    if "dimension_scores" in source:
        result["dimension_scores"] = _normalize_dimension_scores(source.get("dimension_scores"))
    if "avg_score" in source:
        result["avg_score"] = _finite_number(source.get("avg_score"))
    return result


def _normalized_operation_result(
    value,
    *,
    topic: str | None,
    session_summary: str,
    weak_points: list,
    strong_points: list,
    topic_mastery: dict,
    communication: dict,
    thinking_patterns: dict | None,
    avg_score,
    dimension_scores: dict | None,
):
    """Normalize a cached extraction while preserving absent optional fields."""
    if value is None and not any((session_summary, weak_points, strong_points,
                                 topic_mastery, communication, thinking_patterns,
                                 avg_score is not None, dimension_scores)):
        return None
    source = value if isinstance(value, dict) else {}
    result = _normalize_extraction(source, topic)
    candidates = {
        "session_summary": session_summary,
        "weak_points": weak_points,
        "strong_points": strong_points,
        "topic_mastery": topic_mastery,
        "communication_observations": communication,
        "thinking_patterns": thinking_patterns or {},
        "avg_score": avg_score,
        "dimension_scores": dimension_scores or {},
    }
    for key, candidate in candidates.items():
        if key in source or candidate not in ("", [], {}, None):
            result[key] = copy.deepcopy(candidate)
    return result


def _point_text(point) -> str:
    if isinstance(point, str):
        return _clean_text(point)
    if isinstance(point, dict):
        return _clean_text(point.get("point"))
    return ""


def _point_topic(point, fallback: str | None = None) -> str:
    if isinstance(point, dict):
        topic = _clean_text(point.get("topic"))
        if topic:
            return topic
    return fallback or ""


def _operation_artifact_payload(
    user_id: str,
    operation_id: str | None,
    topic: str | None,
    *,
    fallback_summary: str,
    fallback_weak_points: list,
    fallback_strong_points: list,
) -> tuple[str, list, list]:
    """Prefer the journal winner's extraction over a concurrent caller's data."""
    winner = _profile_operation_result(user_id, operation_id)
    if winner is None:
        return fallback_summary, fallback_weak_points, fallback_strong_points
    extraction = _normalize_extraction(winner, topic)
    return (
        extraction.get("session_summary", ""),
        extraction.get("weak_points", []),
        extraction.get("strong_points", []),
    )


def _save_insight(mode: str, topic: str | None, summary: str, raw_extraction: dict,
                  user_id: str, operation_id: str | None = None) -> bool:
    """Append one crash-atomic, operation-idempotent daily insight."""
    ins_dir = _insights_dir(user_id)
    ins_dir.mkdir(parents=True, exist_ok=True)
    topic = _clean_text(topic) or None
    summary = _clean_text(summary)
    extraction = _normalize_extraction(raw_extraction, topic)
    weak_points = extraction.get("weak_points", [])
    strong_points = extraction.get("strong_points", [])
    marker = ""
    if operation_id:
        digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        marker = f"<!-- profile-operation:{digest} -->\n"

    # Every daily file participates in one operation journal. A retry can run
    # after midnight, so both marker lookup and append must share a directory
    # lock rather than independent per-day locks.
    with exclusive_file_lock(file_mutation_lock_path(ins_dir)):
        if marker:
            for insight_path in ins_dir.glob("*.md"):
                if not insight_path.is_file():
                    continue
                existing = insight_path.read_text(encoding="utf-8", errors="replace")
                if marker in existing:
                    return False

        now = datetime.now()
        path = ins_dir / f"{now:%Y-%m-%d}.md"
        existing = (
            path.read_text(encoding="utf-8", errors="replace")
            if path.exists()
            else ""
        )
        entry = f"\n{marker}## {now:%H:%M} | {mode} | {topic or '综合'}\n\n{summary}\n"

        rendered_weak = [
            (_point_text(point), _point_topic(point, topic))
            for point in weak_points
            if _point_text(point)
        ]
        if rendered_weak:
            entry += "\n**薄弱点:**\n"
            for point, point_topic in rendered_weak:
                entry += f"- {point} ({point_topic})\n"

        rendered_strong = [
            (_point_text(point), _point_topic(point, topic))
            for point in strong_points
            if _point_text(point)
        ]
        if rendered_strong:
            entry += "\n**亮点:**\n"
            for point, point_topic in rendered_strong:
                entry += f"- {point} ({point_topic})\n"

        entry += "\n---\n"
        atomic_write_text(path, existing + entry)
    return True


async def _ensure_profile_artifacts(
    *,
    mode: str,
    topic: str | None,
    session_summary: str,
    weak_points: list[dict],
    strong_points: list[dict],
    user_id: str,
    operation_id: str | None,
) -> None:
    """Finish derived profile artifacts before the session step is committed."""
    topic = _clean_text(topic) or None
    session_summary = _clean_text(session_summary)
    weak_points = _normalize_points(weak_points, topic)
    strong_points = _normalize_points(strong_points, topic)
    await asyncio.to_thread(
        _save_insight,
        mode,
        topic,
        session_summary,
        {"weak_points": weak_points, "strong_points": strong_points},
        user_id,
        operation_id,
    )

    if operation_id:
        # A stable session id makes retries upserts in both vector backends.
        # Await completion so a process exit cannot strand a queued-only write
        # after the session has already been marked synchronized.
        from backend.vector_memory import index_session_memory
        await index_session_memory(
            session_id=operation_id,
            topic=topic,
            summary=session_summary,
            weak_points=weak_points,
            strong_points=strong_points,
            insight_text=session_summary,
            user_id=user_id,
            require_complete=True,
        )
        return

    from backend.embedding_tasks import schedule_session_memory_index
    schedule_session_memory_index(
        session_id=None,
        topic=topic,
        summary=session_summary,
        weak_points=weak_points,
        strong_points=strong_points,
        insight_text=session_summary,
        user_id=user_id,
    )


def get_profile(user_id: str) -> dict:
    profile = _load_profile(user_id)
    # The operation journal is an internal recovery detail and should not be
    # rendered by profile APIs or included in assistant context.
    profile.pop(_PROFILE_OPERATIONS_KEY, None)
    return profile


def get_topic_context_for_drill(topic: str, user_id: str) -> dict:
    """Get personalized context for drill question generation."""
    profile = _load_profile(user_id)

    mastery = profile.get("topic_mastery", {}).get(topic, {})
    # profile.json is hand-editable — "score": null must degrade to the level
    # fallback, not flow None into downstream numeric comparisons.
    mastery_score = mastery.get("score")
    if mastery_score is None:
        mastery_score = (mastery.get("level") or 0) * 20
    mastery_notes = mastery.get("notes", "新领域，暂无历史数据" if mastery_score == 0 else "")
    mastery_info = f"{mastery_score}/100 — {mastery_notes}"

    # Weak points for this topic
    topic_weak = [
        w["point"] for w in profile.get("weak_points", [])
        if w.get("topic") == topic and not w.get("improved")
    ]

    # Recent questions from score_history for this topic
    recent_questions = [
        h.get("question", "")
        for h in profile.get("stats", {}).get("score_history", [])
        if h.get("topic") == topic and h.get("question")
    ][-20:]  # last 20

    # Semantic retrieval of past insights for this topic
    past_insights = []
    try:
        from backend.vector_memory import search_memory_sync
        results = search_memory_sync(
            query=f"{topic} 面试薄弱点 常见错误",
            chunk_types=["session_summary", "insight"],
            topic=topic,
            user_id=user_id,
            top_k=3,
        )
        past_insights = [r["content"] for r in results if r["score"] > 0.3]
    except Exception:
        pass  # vector table may not exist yet

    return {
        "mastery_info": mastery_info,
        "mastery_score": mastery_score,
        "weak_points": topic_weak,
        "recent_questions": recent_questions,
        "past_insights": past_insights,
    }


def update_profile_realtime(
    mode: str,
    topic: str | None,
    user_id: str,
    score_entry: dict | None = None,
    weak_point: str | None = None,
):
    """Lightweight per-answer profile update — no LLM call, just save the data."""
    now = datetime.now().isoformat()

    # Resolve the semantic match against a snapshot BEFORE taking the lock — the
    # embedding call can take seconds and must not stall other profile writers.
    # Failure here must not lose the score entry below, so degrade to "no match"
    # (worst case: a near-duplicate weak point gets appended).
    match_point: str | None = None
    if weak_point:
        from backend.vector_memory import find_similar_weak_point_sync
        snapshot_wps = _load_profile(user_id).get("weak_points", [])
        try:
            match_idx = find_similar_weak_point_sync(weak_point, snapshot_wps, user_id=user_id)
            if match_idx is not None:
                match_point = snapshot_wps[match_idx].get("point")
        except Exception as e:
            logger.warning("weak point semantic match failed (%s), treating as new", e)

    with profile_transaction(user_id) as profile:
        # Record score
        if score_entry and score_entry.get("score") is not None:
            history = profile.setdefault("stats", {}).setdefault("score_history", [])
            history.append({
                "date": now[:10],
                "mode": mode,
                "topic": topic,
                "avg_score": score_entry["score"],
                "question": score_entry.get("question", "")[:80],
                "assessment": score_entry.get("assessment", ""),
            })
            # Cap score_history at 100 entries
            if len(history) > 100:
                history[:] = history[-100:]
            # Rolling average ("is not None", not truthiness: a 0-score answer
            # must drag the average down, not be silently excluded from it)
            recent = [h["avg_score"] for h in history[-30:] if h.get("avg_score") is not None]
            if recent:
                profile["stats"]["avg_score"] = round(sum(recent) / len(recent), 1)

        # Record weak point (semantic match resolved above; re-locate by point
        # text because the list may have shifted while we were embedding)
        if weak_point:
            target = None
            if match_point is not None:
                target = next(
                    (wp for wp in profile.get("weak_points", []) if wp.get("point") == match_point),
                    None,
                )
            if target is not None:
                target["times_seen"] = target.get("times_seen", 1) + 1
                target["last_seen"] = now
            else:
                profile.setdefault("weak_points", []).append({
                    "point": weak_point,
                    "topic": topic or "",
                    "first_seen": now,
                    "last_seen": now,
                    "times_seen": 1,
                    "improved": False,
                    "mastery": 20,    # 默认 20 = "未证明"
                    "attempts": 0,
                })

        # Track that we have activity (for profile page display)
        profile.setdefault("stats", {}).setdefault("total_answers", 0)
        profile["stats"]["total_answers"] = profile["stats"].get("total_answers", 0) + 1


def get_profile_summary(user_id: str) -> str:
    """Generate a concise summary for injection into interviewer prompts."""
    profile = _load_profile(user_id)

    parts = []
    if profile.get("weak_points"):
        active_weak = [w for w in profile["weak_points"] if not w.get("improved")]
        if active_weak:
            points = ", ".join(w["point"] for w in active_weak[:8])
            parts.append(f"已知薄弱点: {points}")

    if profile.get("strong_points"):
        points = ", ".join(s["point"] for s in profile["strong_points"][:5])
        parts.append(f"强项: {points}")

    if profile.get("communication", {}).get("style"):
        parts.append(f"沟通风格: {profile['communication']['style']}")

    tp = profile.get("thinking_patterns", {})
    if tp.get("gaps"):
        parts.append(f"思维短板: {', '.join(tp['gaps'][:5])}")
    if tp.get("strengths"):
        parts.append(f"思维优势: {', '.join(tp['strengths'][:5])}")

    if profile.get("stats", {}).get("total_sessions"):
        stats = profile["stats"]
        parts.append(f"已完成 {stats['total_sessions']} 次模拟面试")

    if profile.get("topic_mastery"):
        mastery = ", ".join(
            f"{t}: {v.get('score', v.get('level', 0) * 20)}/100"
            for t, v in profile["topic_mastery"].items()
        )
        parts.append(f"掌握度: {mastery}")

    # 用户偏好
    prefs = profile.get("preferences", {})
    pref_parts = []
    if prefs.get("response_style"):
        pref_parts.append(f"回复风格={prefs['response_style']}")
    if prefs.get("preferred_difficulty"):
        pref_parts.append(f"偏好难度={prefs['preferred_difficulty']}")
    if prefs.get("focus_topics"):
        pref_parts.append(f"重点关注={'/'.join(prefs['focus_topics'])}")
    if prefs.get("interview_pace"):
        pref_parts.append(f"面试节奏={prefs['interview_pace']}")
    if prefs.get("feedback_style"):
        pref_parts.append(f"反馈风格={prefs['feedback_style']}")
    if pref_parts:
        parts.append(f"用户偏好: {', '.join(pref_parts)}")

    return "\n".join(parts) if parts else "新用户，暂无历史数据"


def get_profile_summary_for_drill(user_id: str) -> str:
    """Concise summary for drill question generation — only cross-topic info."""
    profile = _load_profile(user_id)
    parts = []

    if profile.get("communication", {}).get("style"):
        parts.append(f"沟通风格: {profile['communication']['style']}")

    tp = profile.get("thinking_patterns", {})
    if tp.get("gaps"):
        parts.append(f"思维短板: {', '.join(tp['gaps'][:5])}")
    if tp.get("strengths"):
        parts.append(f"思维优势: {', '.join(tp['strengths'][:5])}")

    if profile.get("stats", {}).get("total_sessions"):
        parts.append(f"已完成 {profile['stats']['total_sessions']} 次模拟面试")

    return "\n".join(parts) if parts else "新用户，暂无历史数据"


# ── Mem0-style LLM profile update ──

def _parse_json_safe(content: str) -> dict | list:
    """Parse JSON from LLM response, handling markdown code blocks."""
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", content)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    for i, c in enumerate(content):
        if c in ("[", "{"):
            try:
                return json.loads(content[i:])
            except json.JSONDecodeError:
                pass
            break
    raise json.JSONDecodeError("No valid JSON found", content, 0)


def _apply_memory_ops(profile: dict, ops: dict, topic: str | None, now: str,
                      index_points: list[str] | None = None):
    """Execute LLM-decided ADD/UPDATE/NOOP/IMPROVE operations on profile.

    ``index_points`` is the weak-point text list of the SNAPSHOT the ops were
    generated against. The live list may have shifted while the LLM ran
    (realtime updates append concurrently), so op indices are remapped via the
    snapshot's point text instead of being trusted as positions.
    """
    weak_points = profile.setdefault("weak_points", [])
    if not isinstance(weak_points, list):
        weak_points = []
        profile["weak_points"] = weak_points
    if index_points is not None and not isinstance(index_points, list):
        index_points = None

    def _coerce_index(value) -> int | None:
        # LLMs frequently serialize JSON indexes as strings. Accept only
        # canonical finite integer values; booleans/floats are never valid
        # positions and must not reach range comparisons below.
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            text = value.strip()
            if text and (text.isdigit() or (text.startswith("-") and text[1:].isdigit())):
                try:
                    return int(text)
                except ValueError:
                    return None
        return None

    def _resolve(raw_idx) -> int | None:
        idx = _coerce_index(raw_idx)
        if idx is None:
            return None
        if index_points is not None:
            if not (0 <= idx < len(index_points)):
                return None
            text = index_points[idx]
            for i, wp in enumerate(weak_points):
                if isinstance(wp, dict) and wp.get("point") == text:
                    return i
            return None
        return idx if 0 <= idx < len(weak_points) else None

    for op in ops.get("weak_point_ops", []):
        action = op.get("action", "NOOP")
        if action == "ADD":
            point = op.get("point")
            if not point:
                continue
            weak_points.append({
                "point": point,
                "topic": op.get("topic", topic or ""),
                "first_seen": now, "last_seen": now,
                "times_seen": 1, "improved": False,
                "mastery": 20, "attempts": 0,
            })
        elif action == "UPDATE":
            idx = _resolve(op.get("index"))
            if idx is not None:
                wp = weak_points[idx]
                if not isinstance(wp, dict):
                    continue
                if op.get("new_point"):
                    wp["point"] = op["new_point"]
                wp["times_seen"] = wp.get("times_seen", 1) + 1
                wp["last_seen"] = now

    for imp in ops.get("improvements", []):
        idx = _resolve(imp.get("weak_index"))
        if idx is not None:
            if isinstance(weak_points[idx], dict):
                weak_points[idx]["improved"] = True
                weak_points[idx]["improved_at"] = now

    strong_points = profile.setdefault("strong_points", [])
    if not isinstance(strong_points, list):
        strong_points = []
        profile["strong_points"] = strong_points
    existing_strong = {
        s.get("point") for s in strong_points
        if isinstance(s, dict) and isinstance(s.get("point"), str)
    }
    for op in ops.get("strong_point_ops", []):
        if op.get("action") == "ADD" and op.get("point") and op["point"] not in existing_strong:
            strong_points.append({
                "point": op["point"],
                "topic": op.get("topic", topic or ""),
                "first_seen": now,
            })


async def _resolve_new_weak_points(
    snapshot: dict, new_weak: list, topic: str | None, user_id: str,
) -> list[tuple[str, str | None, str]]:
    """Deterministic-fallback phase 1 (runs OUTSIDE the profile lock).

    Resolves each new weak point against a snapshot via embedding similarity
    and returns ``(point, matched_point_text_or_None, topic)`` tuples. New
    points still dedup against ones resolved earlier in the same batch — the
    working list grows as we go. Embedding failures degrade to "no match"
    rather than aborting the whole update.
    """
    from backend.vector_memory import find_similar_weak_point

    working = list(snapshot.get("weak_points", []))
    resolved: list[tuple[str, str | None, str]] = []
    for wp in new_weak:
        point = wp.get("point", wp) if isinstance(wp, dict) else str(wp)
        t = wp.get("topic", topic) if isinstance(wp, dict) else (topic or "")
        try:
            match_idx = await find_similar_weak_point(point, working, user_id=user_id)
        except Exception as e:
            logger.warning("weak point semantic match failed (%s), treating as new", e)
            match_idx = None
        if match_idx is not None:
            resolved.append((point, working[match_idx].get("point"), t or ""))
        else:
            working.append({"point": point, "topic": t or ""})
            resolved.append((point, None, t or ""))
    return resolved


def _apply_deterministic_update(profile: dict, resolved: list, new_strong: list,
                                topic: str | None, now: str):
    """Deterministic-fallback phase 2 (runs INSIDE the profile lock).

    Applies pre-resolved weak-point matches to the FRESH profile, re-locating
    matches by point text (the list may have shifted since the snapshot).
    """
    for point, matched_text, t in resolved:
        target = None
        if matched_text is not None:
            target = next(
                (w for w in profile.get("weak_points", []) if w.get("point") == matched_text),
                None,
            )
        if target is not None:
            target["times_seen"] = target.get("times_seen", 1) + 1
            target["last_seen"] = now
        else:
            profile.setdefault("weak_points", []).append({
                "point": point,
                "topic": t,
                "first_seen": now, "last_seen": now,
                "times_seen": 1, "improved": False,
                "mastery": 20, "attempts": 0,
            })

    for sp in new_strong:
        sp_text = sp.get("point", sp) if isinstance(sp, dict) else str(sp)
        for w in profile.get("weak_points", []):
            if w.get("topic") == (sp.get("topic") if isinstance(sp, dict) else topic) and not w.get("improved"):
                w["improved"] = True
                w["improved_at"] = now
                break
        existing = {s["point"] for s in profile.get("strong_points", [])}
        if sp_text not in existing:
            profile.setdefault("strong_points", []).append({
                "point": sp_text,
                "topic": sp.get("topic") if isinstance(sp, dict) else (topic or ""),
                "first_seen": now,
            })


def _update_mastery(profile: dict, topic: str | None, mastery_data: dict, now: str,
                    session_weight: float = 0.7):
    """Update topic mastery (0-100 scale). session_weight controls merge ratio."""
    if not mastery_data:
        return
    # {score, notes} → single topic; {topic_key: {score, notes}} → multi-topic
    if "score" in mastery_data or "level" in mastery_data:
        if not topic:
            return
        entries = {topic: mastery_data}
    else:
        entries = mastery_data

    for t, data in entries.items():
        if not isinstance(data, dict):
            continue
        existing = profile.setdefault("topic_mastery", {}).setdefault(t, {})
        new_score = data.get("score")
        if new_score is not None:
            # Backward compat: convert old Lv1-5 to 0-100
            old_score = existing.get("score", existing.get("level", 0) * 20)
            merged = round(old_score * (1 - session_weight) + new_score * session_weight, 1)
            existing["score"] = merged
            existing.pop("level", None)
        if data.get("notes"):
            existing["notes"] = data["notes"]
        existing["last_assessed"] = now


def _update_communication(profile: dict, comm: dict):
    """Append communication observations."""
    if not comm:
        return
    if comm.get("style_update"):
        profile.setdefault("communication", {})["style"] = comm["style_update"]
    for habit in comm.get("new_habits", []):
        habits = profile.setdefault("communication", {}).setdefault("habits", [])
        if habit not in habits:
            habits.append(habit)
    for sug in comm.get("new_suggestions", []):
        suggestions = profile.setdefault("communication", {}).setdefault("suggestions", [])
        if sug not in suggestions:
            suggestions.append(sug)


def _update_thinking_patterns(profile: dict, patterns: dict):
    """Append thinking pattern observations."""
    if not patterns:
        return
    tp = profile.setdefault("thinking_patterns", {"strengths": [], "gaps": []})
    for s in patterns.get("new_strengths", []):
        if s not in tp["strengths"]:
            tp["strengths"].append(s)
    for g in patterns.get("new_gaps", []):
        if g not in tp["gaps"]:
            tp["gaps"].append(g)


def _update_stats(
    profile: dict, mode: str, topic: str | None, avg_score: float | None,
    now: str, answer_count: int = 0, dimension_scores: dict | None = None,
):
    """Update session statistics with per-mode averages."""
    stats = profile.setdefault("stats", {})
    stats["total_sessions"] = stats.get("total_sessions", 0) + 1
    if mode == "resume":
        stats["resume_sessions"] = stats.get("resume_sessions", 0) + 1
    elif mode == "topic_drill":
        stats["drill_sessions"] = stats.get("drill_sessions", 0) + 1
    elif mode == "jd_prep":
        stats["job_prep_sessions"] = stats.get("job_prep_sessions", 0) + 1

    if answer_count:
        stats["total_answers"] = stats.get("total_answers", 0) + answer_count

    # "is not None", not truthiness: a 0-score session must still be recorded
    # and pull the averages down instead of being silently skipped.
    if avg_score is not None:
        history = stats.setdefault("score_history", [])
        entry = {"date": now[:10], "mode": mode, "topic": topic, "avg_score": avg_score}
        if dimension_scores:
            entry["dimension_scores"] = dimension_scores
        history.append(entry)
        # Cap score_history at 100 entries
        if len(history) > 100:
            history[:] = history[-100:]

        # Per-mode rolling averages
        drill_scores = [h["avg_score"] for h in history if h.get("mode") == "topic_drill" and h.get("avg_score") is not None][-20:]
        resume_scores = [h["avg_score"] for h in history if h.get("mode") == "resume" and h.get("avg_score") is not None][-10:]
        job_prep_scores = [h["avg_score"] for h in history if h.get("mode") == "jd_prep" and h.get("avg_score") is not None][-10:]

        if drill_scores:
            stats["drill_avg_score"] = round(sum(drill_scores) / len(drill_scores), 1)
        if resume_scores:
            stats["resume_avg_score"] = round(sum(resume_scores) / len(resume_scores), 1)
        if job_prep_scores:
            stats["job_prep_avg_score"] = round(sum(job_prep_scores) / len(job_prep_scores), 1)

        all_recent = [h["avg_score"] for h in history if h.get("avg_score") is not None][-30:]
        if all_recent:
            stats["avg_score"] = round(sum(all_recent) / len(all_recent), 1)


async def llm_update_profile(
    mode: str,
    topic: str | None,
    new_weak_points: list[dict],
    new_strong_points: list[dict],
    topic_mastery: dict,
    communication: dict,
    user_id: str,
    thinking_patterns: dict | None = None,
    session_summary: str = "",
    avg_score: float | None = None,
    answer_count: int = 0,
    session_weight: float = 0.7,
    dimension_scores: dict | None = None,
    operation_id: str | None = None,
    operation_result: dict | None = None,
) -> bool:
    """Mem0-style profile update: LLM decides ADD/UPDATE/NOOP for each fact."""
    from backend.prompts.interviewer import PROFILE_UPDATE_PROMPT

    topic = _clean_text(topic) or None
    session_summary = _clean_text(session_summary)
    new_weak_points = _normalize_points(new_weak_points, topic)
    new_strong_points = _normalize_points(new_strong_points, topic)
    topic_mastery = _normalize_mastery(topic_mastery)
    communication = _normalize_observations(
        communication,
        fields=("style_update", "new_habits", "new_suggestions"),
    )
    thinking_patterns = _normalize_observations(
        thinking_patterns,
        fields=("new_strengths", "new_gaps"),
    )
    avg_score = _finite_number(avg_score)
    dimension_scores = _normalize_dimension_scores(dimension_scores)
    session_weight = _finite_number(session_weight)
    if session_weight is None:
        session_weight = 0.7
    else:
        session_weight = min(1.0, max(0.0, session_weight))
    operation_result = _normalized_operation_result(
        operation_result,
        topic=topic,
        session_summary=session_summary,
        weak_points=new_weak_points,
        strong_points=new_strong_points,
        topic_mastery=topic_mastery,
        communication=communication,
        thinking_patterns=thinking_patterns,
        avg_score=avg_score,
        dimension_scores=dimension_scores,
    )

    # Snapshot for prompt formatting + index remapping. The LLM call below can
    # take tens of seconds; the profile is re-read fresh inside the transaction
    # so concurrent realtime updates landing meanwhile aren't overwritten.
    snapshot = _load_profile(user_id)
    if _get_applied_operation(snapshot, operation_id) is not None:
        artifact_summary, artifact_weak, artifact_strong = _operation_artifact_payload(
            user_id,
            operation_id,
            topic,
            fallback_summary=session_summary,
            fallback_weak_points=new_weak_points,
            fallback_strong_points=new_strong_points,
        )
        await _ensure_profile_artifacts(
            mode=mode,
            topic=topic,
            session_summary=artifact_summary,
            weak_points=artifact_weak,
            strong_points=artifact_strong,
            user_id=user_id,
            operation_id=operation_id,
        )
        return False
    now = datetime.now().isoformat()

    # ── LLM-based update for weak/strong points (slow part — OUTSIDE the lock) ──
    has_new_facts = bool(new_weak_points or new_strong_points)
    ops: dict | None = None
    resolved_fallback: list | None = None
    snapshot_points = [wp.get("point") for wp in snapshot.get("weak_points", [])]

    if has_new_facts:
        # Format existing points with indices for LLM reference
        existing_weak_lines = []
        for i, wp in enumerate(snapshot.get("weak_points", [])):
            status = "已改善" if wp.get("improved") else f"出现{wp.get('times_seen', 1)}次"
            existing_weak_lines.append(
                f"[{i}] {wp['point']} (领域: {wp.get('topic', '?')}, {status})"
            )
        existing_strong_lines = []
        for i, sp in enumerate(snapshot.get("strong_points", [])):
            existing_strong_lines.append(f"[{i}] {sp['point']} (领域: {sp.get('topic', '?')})")

        new_weak_lines = []
        for wp in new_weak_points:
            point = wp.get("point", wp) if isinstance(wp, dict) else str(wp)
            t = wp.get("topic", topic) if isinstance(wp, dict) else topic
            new_weak_lines.append(f"- {point} (领域: {t})")
        new_strong_lines = []
        for sp in new_strong_points:
            point = sp.get("point", sp) if isinstance(sp, dict) else str(sp)
            t = sp.get("topic", topic) if isinstance(sp, dict) else topic
            new_strong_lines.append(f"- {point} (领域: {t})")

        prompt = PROFILE_UPDATE_PROMPT.format(
            existing_weak="\n".join(existing_weak_lines) or "暂无",
            existing_strong="\n".join(existing_strong_lines) or "暂无",
            new_weak="\n".join(new_weak_lines) or "暂无",
            new_strong="\n".join(new_strong_lines) or "暂无",
        )

        llm = get_langchain_llm()
        response = await llm.ainvoke([
            SystemMessage(content="你是画像更新引擎。只返回 JSON。"),
            HumanMessage(content=prompt),
        ])

        try:
            parsed = _parse_json_safe(response.content)
            if isinstance(parsed, dict):
                ops = parsed
            else:
                raise ValueError(f"Expected dict, got {type(parsed)}")
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Profile update LLM parse failed ({e}), falling back to deterministic")
            resolved_fallback = await _resolve_new_weak_points(
                snapshot, new_weak_points, topic, user_id,
            )

    # ── Apply everything to a FRESH profile under the lock (fast, no awaits) ──
    applied = False
    with profile_transaction(user_id) as profile:
        # Another worker may finish while this one waits on the LLM. This check
        # and the marker write are protected by the same profile transaction.
        if _get_applied_operation(profile, operation_id) is not None:
            raise ProfileTransactionAbort
        if ops is not None:
            _apply_memory_ops(profile, ops, topic, now, index_points=snapshot_points)
        elif resolved_fallback is not None:
            _apply_deterministic_update(profile, resolved_fallback, new_strong_points, topic, now)

        # Snapshot current mastery for frontend comparison overlay
        current_mastery = profile.get("topic_mastery", {})
        if current_mastery:
            profile["previous_topic_mastery"] = copy.deepcopy(current_mastery)

        # Deterministic updates for mastery / communication / thinking / stats
        _update_mastery(profile, topic, topic_mastery, now, session_weight)
        _update_communication(profile, communication)
        _update_thinking_patterns(profile, thinking_patterns)
        _update_stats(profile, mode, topic, avg_score, now, answer_count, dimension_scores)
        _record_applied_operation(
            profile, operation_id, result=operation_result,
        )
        applied = True

    if not applied:
        artifact_summary, artifact_weak, artifact_strong = _operation_artifact_payload(
            user_id,
            operation_id,
            topic,
            fallback_summary=session_summary,
            fallback_weak_points=new_weak_points,
            fallback_strong_points=new_strong_points,
        )
        await _ensure_profile_artifacts(
            mode=mode,
            topic=topic,
            session_summary=artifact_summary,
            weak_points=artifact_weak,
            strong_points=artifact_strong,
            user_id=user_id,
            operation_id=operation_id,
        )
        return False

    await _ensure_profile_artifacts(
        mode=mode,
        topic=topic,
        session_summary=session_summary,
        weak_points=new_weak_points,
        strong_points=new_strong_points,
        user_id=user_id,
        operation_id=operation_id,
    )
    return True


async def update_profile_after_interview(
    mode: str,
    topic: str | None,
    messages: list,
    user_id: str,
    scores: list[dict] | None = None,
    operation_id: str | None = None,
) -> dict:
    """Mem0-style two-stage pipeline: Extract → Update."""
    profile = _load_profile(user_id)
    operation = _get_applied_operation(profile, operation_id)
    if operation is not None:
        # Re-read through the journal helper so this branch and the
        # llm_update_profile race-loser branches share winner semantics.
        result = _profile_operation_result(user_id, operation_id)
        extraction = _normalize_extraction(result, topic)
        await _ensure_profile_artifacts(
            mode=mode,
            topic=topic,
            session_summary=extraction.get("session_summary", ""),
            weak_points=extraction.get("weak_points", []),
            strong_points=extraction.get("strong_points", []),
            user_id=user_id,
            operation_id=operation_id,
        )
        return extraction
    llm = get_langchain_llm()

    # ── Stage 1: Extract insights ──
    transcript_lines = []
    for msg in messages:
        if hasattr(msg, "content"):
            if isinstance(msg, HumanMessage):
                transcript_lines.append(f"候选人: {msg.content}")
            elif hasattr(msg, "content") and not isinstance(msg, SystemMessage):
                transcript_lines.append(f"面试官: {msg.content}")

    score_text = ""
    if scores:
        score_text = "\n".join(
            f"- Q: {s.get('question', '?')} → {s.get('score', '?')}/10 ({s.get('assessment', '')})"
            for s in scores
        )

    # Window-derived budget for the two big extraction inputs (profile + transcript)
    # instead of the old [:2000]-char profile / last-60-lines transcript cuts. The
    # transcript is the primary extraction signal so it wins the budget; the prior
    # profile is secondary.
    from backend.context_assembler import ContextBudget, Section, resolve_input_budget, count_tokens
    _fixed_extract = EXTRACT_PROMPT.format(
        current_profile="", mode=mode, topic=topic or "综合", transcript="", scores=score_text or "无",
    )
    profile_context = copy.deepcopy(profile)
    profile_context.pop(_PROFILE_OPERATIONS_KEY, None)
    _ex_packed = ContextBudget(max(1000, resolve_input_budget() - count_tokens(_fixed_extract))).pack([
        Section("transcript", "\n".join(transcript_lines), priority=1, min_tokens=300),
        Section("profile", json.dumps(profile_context, ensure_ascii=False), priority=2, min_tokens=200),
    ])
    extract_msg = EXTRACT_PROMPT.format(
        current_profile=_ex_packed.get("profile"),
        mode=mode,
        topic=topic or "综合",
        transcript=_ex_packed.get("transcript"),
        scores=score_text or "无",
    )

    response = await llm.ainvoke([
        SystemMessage(content="你是面试分析引擎。只返回 JSON。"),
        HumanMessage(content=extract_msg),
    ])

    try:
        # Reuse the hardened parser (handles raw JSON, ```json fences, and
        # leading prose) instead of a fragile split("```")[1] that breaks on
        # unfenced output and silently discarded the whole session's signal.
        extraction = _parse_json_safe(response.content)
        if not isinstance(extraction, dict):
            raise ValueError("extraction is not a JSON object")
    except (json.JSONDecodeError, ValueError):
        extraction = {"session_summary": "提取失败", "weak_points": [], "strong_points": []}

    # An LLM can return syntactically valid JSON with malformed field shapes.
    # Normalize before llm_update_profile writes its durable operation marker,
    # so every cached retry remains safe to replay into insight/vector artifacts.
    extraction = _normalize_extraction(extraction, topic)

    # ── Stage 2: LLM-based Update (Mem0 style) ──
    applied = await llm_update_profile(
        mode=mode,
        topic=topic,
        new_weak_points=extraction.get("weak_points", []),
        new_strong_points=extraction.get("strong_points", []),
        topic_mastery=extraction.get("topic_mastery", {}),
        communication=extraction.get("communication_observations", {}),
        user_id=user_id,
        thinking_patterns=extraction.get("thinking_patterns"),
        session_summary=extraction.get("session_summary", ""),
        avg_score=extraction.get("avg_score"),
        dimension_scores=extraction.get("dimension_scores"),
        operation_id=operation_id,
        operation_result=extraction,
    )

    if not applied and operation_id:
        stored_result = _profile_operation_result(user_id, operation_id)
        if stored_result is not None:
            return _normalize_extraction(stored_result, topic)

    return extraction
