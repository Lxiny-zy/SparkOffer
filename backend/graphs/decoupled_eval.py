"""Phase 5A: decoupled drill evaluation.

Replaces the single big-batch eval with:
1. Per-question scoring fanned out in parallel on the *small* LLM tier.
2. One *large* LLM call that sees only the score statistics, producing
   the overall observation + new weak_points / strong_points.

Benefit: 10 small parallel calls + 1 large call total ≤ wall-clock of the
old single big call, AND eliminates score contamination (the big model used
to anchor later questions on earlier ones, biasing the batch).

The legacy ``evaluate_drill_answers`` / ``stream_evaluate_drill_answers``
stay in place — they are the fallback when the small tier isn't configured.
"""
from __future__ import annotations

import asyncio
import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from backend.channel_manager import get_all_channels
from backend.graphs.topic_drill import (
    _get_topic_display, _parse_json_response, _safe_retrieve,
)
from backend.llm_provider import get_langchain_llm
from backend.memory import get_profile_summary_for_drill
from backend.prompts._common import SCORING_RUBRIC
from backend.prompts.interviewer import (
    DRILL_PER_QUESTION_SCORE_PROMPT, DRILL_OVERALL_SUMMARY_PROMPT,
)

logger = logging.getLogger("uvicorn")

# 单题评分一次性 fan-out 最多 10 路 small-tier ainvoke。无限流会打爆上游
# 并发额度（见 commit 5ce4d7b 的 DashScope 429）。沿用 rag_eval._LLM_CONCURRENCY
# 的做法：semaphore 在调用 gather 的协程内创建（事件循环内），避免跨事件循环绑定。
_SCORE_CONCURRENCY = 4


def has_small_tier() -> bool:
    """True when at least one enabled LLM channel is marked tier=small."""
    for ch in get_all_channels("llm"):
        if ch.get("enabled", True) and (ch.get("tier") or "large").lower() == "small":
            return True
    return False


async def evaluate_decoupled(
    topic: str,
    questions: list[dict],
    answers: list[dict],
    user_id: str,
    progress_cb=None,
) -> dict:
    """Run per-Q scoring in parallel on small tier, then summarize on large tier.

    ``progress_cb(done, total)`` (optional) fires as each per-question score
    completes, so the SSE layer can stream "评分 X/N 题" step progress.

    Returns the same shape the legacy evaluator returns:
        {"scores": [...], "overall": {...}}
    """
    topic_display = _get_topic_display(user_id)
    topic_name = topic_display.get(topic, topic)
    answer_map = {a["question_id"]: a.get("answer", "") for a in answers if a.get("question_id")}

    # Empty answers should record a zero-score row, not vanish silently —
    # otherwise the user's profile mastery stays artificially high for
    # questions they actually skipped.
    answered: list[dict] = []
    skipped: list[dict] = []
    for q in questions:
        answer = answer_map.get(q["id"])
        text = (answer or "").strip() if isinstance(answer, str) else ""
        if text:
            answered.append(q)
        else:
            skipped.append(q)

    if not answered and not skipped:
        return {
            "scores": [],
            "overall": {
                "avg_score": None, "summary": "无作答题目",
                "new_weak_points": [], "new_strong_points": [],
            },
        }

    small_llm = get_langchain_llm(tier="small")
    # Bound the per-Q LLM concurrency. Created here (inside the running loop) and
    # captured by the _score_one closure so it's bound to the correct event loop.
    score_sem = asyncio.Semaphore(_SCORE_CONCURRENCY)

    async def _score_one(q: dict) -> dict:
        qid = q["id"]
        answer = answer_map[qid]
        # Reuse the existing safe retrieve helper for per-Q reference. top_k=2
        # so the prompt stays compact for the small model.
        refs = await asyncio.to_thread(_safe_retrieve, topic, q["question"], user_id, 2)
        prompt = DRILL_PER_QUESTION_SCORE_PROMPT.format(
            topic_name=topic_name,
            difficulty=q.get("difficulty", 3),
            question=q["question"],
            answer=answer,
            reference="\n".join(refs)[:800] if refs else "（无参考）",
            scoring_rubric=SCORING_RUBRIC,
            question_id=qid,
        )
        try:
            async with score_sem:
                resp = await small_llm.ainvoke([
                    SystemMessage(content="你是单题评分引擎，只返回 JSON 对象。"),
                    HumanMessage(content=prompt),
                ])
            raw = resp.content if hasattr(resp, "content") else str(resp)
            parsed = _parse_json_response(raw)
            if not isinstance(parsed, dict):
                raise ValueError(f"per-Q parse not a dict: {type(parsed).__name__}")
            # Pin the qid in case the small model omitted/altered it.
            parsed["question_id"] = qid
            parsed.setdefault("difficulty", q.get("difficulty", 3))
            return parsed
        except Exception as exc:
            logger.warning("per-Q score failed for qid=%s: %s", qid, exc)
            return {
                "question_id": qid,
                "score": None,
                "assessment": f"评分失败：{exc}",
                "improvement": "",
                "weak_point": "",
                "difficulty": q.get("difficulty", 3),
            }

    # Wrap each scorer to report completion progress as soon as it resolves.
    # `done += 1` is safe under the single-threaded event loop (no await between
    # the read and write of `done`).
    done = 0
    total = len(answered)

    async def _score_and_report(q: dict) -> dict:
        nonlocal done
        result = await _score_one(q)
        done += 1
        if progress_cb:
            try:
                progress_cb(done, total)
            except Exception:
                pass
        return result

    scores = await asyncio.gather(*[_score_and_report(q) for q in answered])
    scores = list(scores)

    # Skipped questions get an explicit zero so they show up in stats and so
    # downstream EWMA correctly registers "candidate didn't answer this".
    for q in skipped:
        scores.append({
            "question_id": q["id"],
            "score": 0,
            "assessment": "本题未作答。",
            "improvement": "下次记得回答这一题——即便没把握，也写下你的猜测和推导过程。",
            "weak_point": "",
            "difficulty": q.get("difficulty", 3),
            "understanding": "完全跑偏",
            "skipped": True,
        })
    scores.sort(key=lambda s: s.get("question_id", 0))

    # ── overall summary on the large tier, fed only stats ──
    overall = await _summarize_overall(topic, topic_name, scores, user_id)
    return {"scores": scores, "overall": overall}


async def _summarize_overall(topic: str, topic_name: str, scores: list[dict], user_id: str) -> dict:
    valid_scores = [s for s in scores if isinstance(s.get("score"), (int, float))]
    if not valid_scores:
        return {
            "avg_score": None, "summary": "评估失败，请重试。",
            "new_weak_points": [], "new_strong_points": [],
        }

    avg = sum(s["score"] for s in valid_scores) / len(valid_scores)
    by_difficulty: dict[int, list[float]] = {}
    weak_hits: dict[str, int] = {}
    for s in valid_scores:
        d = int(s.get("difficulty", 3))
        by_difficulty.setdefault(d, []).append(float(s["score"]))
        wp = (s.get("weak_point") or "").strip()
        if wp:
            weak_hits[wp] = weak_hits.get(wp, 0) + 1

    stats_lines = [f"- 平均分: {avg:.1f}/10（共 {len(valid_scores)} 题）"]
    for d in sorted(by_difficulty):
        ss = by_difficulty[d]
        stats_lines.append(f"- 难度 {d}/5：{len(ss)} 题，均分 {sum(ss)/len(ss):.1f}")
    if weak_hits:
        stats_lines.append("- 暴露的薄弱点出现次数：")
        for wp, cnt in sorted(weak_hits.items(), key=lambda kv: -kv[1]):
            stats_lines.append(f"  · {wp}: {cnt} 次")

    big_llm = get_langchain_llm(tier="large")
    prompt = DRILL_OVERALL_SUMMARY_PROMPT.format(
        topic_name=topic_name,
        topic_key=topic,
        score_stats="\n".join(stats_lines),
        user_profile=get_profile_summary_for_drill(user_id),
    )
    try:
        resp = await big_llm.ainvoke([
            SystemMessage(content="你是训练评估的整体观察引擎，只返回 JSON 对象。"),
            HumanMessage(content=prompt),
        ])
        raw = resp.content if hasattr(resp, "content") else str(resp)
        parsed = _parse_json_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError(f"overall parse not a dict: {type(parsed).__name__}")
        parsed.setdefault("avg_score", round(avg, 1))
        return _normalize_overall(parsed, topic)
    except Exception as exc:
        logger.warning("overall summary failed: %s", exc)
        return {
            "avg_score": round(avg, 1),
            "summary": f"整体总结失败 ({exc})，仅展示分数。",
            "new_weak_points": [
                {"point": wp, "topic": topic} for wp, cnt in weak_hits.items() if cnt >= 2
            ],
            "new_strong_points": [],
        }


def _normalize_overall(parsed: dict, topic: str) -> dict:
    """Coerce the overall dict to the batch-eval schema (single source of truth).

    The decoupled path historically asked the LLM for flat strings where the
    batch path used nested objects; downstream (llm_update_profile /
    _update_communication / _update_thinking_patterns and the frontend Overall
    type) all consume the nested shape. Normalize here so a model that still
    emits the legacy flat shape can't corrupt the profile.
    """
    def _pointify(items) -> list[dict]:
        out = []
        for it in items or []:
            if isinstance(it, dict) and it.get("point"):
                out.append({"point": str(it["point"]), "topic": it.get("topic") or topic})
            elif isinstance(it, str) and it.strip():
                out.append({"point": it.strip(), "topic": topic})
        return out

    parsed["new_weak_points"] = _pointify(parsed.get("new_weak_points"))
    parsed["new_strong_points"] = _pointify(parsed.get("new_strong_points"))

    comm = parsed.get("communication_observations")
    if isinstance(comm, str):
        comm = {"style_update": comm.strip(), "new_habits": [], "new_suggestions": []}
    elif not isinstance(comm, dict):
        comm = {}
    parsed["communication_observations"] = comm

    tp = parsed.get("thinking_patterns")
    if isinstance(tp, str):
        # A flat string carries no strength/gap split — file it as neither
        # rather than guessing; the summary text already covers it.
        tp = {"new_strengths": [], "new_gaps": []}
    elif not isinstance(tp, dict):
        tp = {}
    parsed["thinking_patterns"] = tp

    # Mastery score is deterministic (difficulty/5 × score/10, computed in
    # _update_drill_profile) — drop any LLM-estimated score, keep only notes.
    tm = parsed.get("topic_mastery")
    parsed["topic_mastery"] = {"notes": tm.get("notes", "")} if isinstance(tm, dict) else {}
    return parsed
