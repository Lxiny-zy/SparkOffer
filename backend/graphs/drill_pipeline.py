"""Drill question-generation pipeline — staged, timed, SSE-streamable.

Goal of this module: turn the single long async generator in
``interview.py:start_interview_stream`` into a sequence of named stages that
each emits ``pipeline_stage`` SSE events with their own duration. The frontend
uses those events to render a live timeline so we can see which stage is the
bottleneck before optimizing it.

Stage map:
    prepare   — load profile / SR / weak_points / topic context
    retrieve  — RAG knowledge-base chunks
    generate  — LLM streaming generation + incremental question emission
    validate  — placeholder hook for Phase 4 (currently a no-op pass-through)
    finalize  — persist session, emit ``done``

Phase 1 scope: NO behavioral change vs the legacy inline implementation. This
file just wraps the same logic into stages, so we have a stable observation
surface for Phases 2-7 to plug into.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator

from langchain_core.messages import HumanMessage, SystemMessage

from backend.graphs.topic_drill import (
    _get_topic_display, _load_high_freq, _parse_json_response,
)
from backend.graphs.rag_retrieval import retrieve_for_drill
from backend.graphs.seed_pool import draft_from_seed_pool, has_pool
from backend.graphs.validators import (
    DEFAULT_VALIDATORS, ValidationContext, ValidationResult,
)
from backend.indexer import safe_retrieve_topic_context
from backend.llm_provider import get_langchain_llm
from backend.live_store import drill_sessions, save_live
from backend.memory import get_profile_summary_for_drill, get_topic_context_for_drill, _load_profile
from backend.prompts.interviewer import (
    DRILL_QUESTION_GEN_PROMPT, DRILL_REPAIR_PROMPT, COLD_START_DRILL_PROMPT,
)
from backend.prompts.strategies import allocate_slots, render_strategy_block, difficulty_range_for_plan
from backend.redis_cache import get_cache
from backend.spaced_repetition import get_due_reviews, init_sr_for_existing_points
from backend.storage.sessions import create_session
from backend.utils.sse_helpers import sse_event
from backend.utils.stream_parser import extract_complete_objects

logger = logging.getLogger("uvicorn")


# ── Stage event helpers ──

STAGE_NAMES = ["prepare", "retrieve", "generate", "validate", "finalize"]

# Frontend-facing display labels — keep in sync with PipelineTimeline.tsx
STAGE_LABELS = {
    "prepare": "准备上下文",
    "retrieve": "检索知识库",
    "generate": "AI 生成题目",
    "validate": "校验",
    "finalize": "持久化",
}


def _stage_event(stage: str, status: str, **extra) -> str:
    payload = {
        "type": "pipeline_stage",
        "stage": stage,
        "label": STAGE_LABELS.get(stage, stage),
        "status": status,
        "ts": time.time(),
        **extra,
    }
    return sse_event(payload)


# ── Pipeline ──


class DrillPipeline:
    """Run the drill-question generation flow as a sequence of timed stages.

    Each stage's start/end is reported via ``pipeline_stage`` SSE events with
    ``duration_ms``. Existing event types (``progress`` / ``question`` /
    ``done`` / ``error``) are kept verbatim so the frontend stays backward
    compatible.
    """

    def __init__(self, topic: str, user_id: str, mode: str = "topic_drill"):
        self.topic = topic
        self.user_id = user_id
        self.mode = mode
        # Mutable state accumulated across stages.
        self.ctx: dict = {}
        self.questions: list[dict] = []
        self.session_id: str = ""

    async def run(self) -> AsyncGenerator[str, None]:
        for stage in STAGE_NAMES:
            t0 = time.perf_counter()
            yield _stage_event(stage, "start")
            try:
                handler = getattr(self, f"_stage_{stage}")
                async for event in handler():
                    yield event
            except Exception as exc:
                duration_ms = (time.perf_counter() - t0) * 1000.0
                logger.exception("DrillPipeline stage=%s failed: %s", stage, exc)
                yield _stage_event(stage, "error", duration_ms=duration_ms, detail=str(exc)[:200])
                yield sse_event({"type": "error", "message": f"{STAGE_LABELS.get(stage, stage)}失败: {exc}"})
                return
            duration_ms = (time.perf_counter() - t0) * 1000.0
            yield _stage_event(stage, "ok", duration_ms=duration_ms, detail=self._stage_detail(stage))

    # ── stage_detail: short string summarizing what the stage produced ──

    def _stage_detail(self, stage: str) -> str:
        if stage == "prepare":
            wps = self.ctx.get("all_weak", [])
            due = self.ctx.get("due_points", [])
            plan = self.ctx.get("plan")
            if plan is not None:
                counts = plan.counts
                parts = [f"{k}={v}" for k, v in counts.items()]
                return f"{len(wps)} WP · {len(due)} 到期 · 槽位 {' '.join(parts)}"
            return f"{len(wps)} 个薄弱点 · {len(due)} 个到期复习"
        if stage == "retrieve":
            n = self.ctx.get("knowledge_chunks", 0)
            queries = self.ctx.get("retrieval_queries", 0)
            if self.ctx.get("knowledge_cache_hit"):
                return f"缓存命中 · {n} 个片段 (跳过 RAG)"
            stats = self.ctx.get("retrieval_stats")
            if stats is not None:
                rerank_seg = {
                    "applied": " · 重排 ✓ 生效",
                    "degraded": " · 重排 ⚠ 降级",
                    "off": " · 重排 未启用",
                }.get(stats.reranker_status, "")
                return (
                    f"{queries} 路检索 · {stats.raw_chunks}→{stats.fused_chunks}→{n} 片段 · "
                    f"缓存 {stats.embed_cache_hits}/{stats.embed_cache_hits + stats.embed_cache_misses}"
                    f"{rerank_seg}"
                )
            return f"{queries} 次检索 · {n} 个去重后片段"
        if stage == "generate":
            seed = self.ctx.get("seed_count", 0)
            if seed:
                return f"生成 {len(self.questions)} 题（{seed} 种子 + {len(self.questions) - seed} LLM）"
            return f"生成 {len(self.questions)} 题"
        if stage == "validate":
            summaries = self.ctx.get("validator_summary") or []
            outcome = self.ctx.get("repair_outcome")
            if outcome:
                return f"{outcome} · {' / '.join(summaries)[:80]}"
            return " / ".join(summaries) or "无校验项"
        if stage == "finalize":
            cal = self.ctx.get("difficulty_calibrated")
            if cal:
                return f"session={self.session_id} · 难度校准 {cal[0]}/{cal[1]}"
            return f"session={self.session_id}"
        return ""

    # ── Stage 1: prepare ──

    async def _stage_prepare(self) -> AsyncGenerator[str, None]:
        # Cheap, blocking-ish work — kept inline (no thread offload) because
        # it's mostly disk + memory reads.
        init_sr_for_existing_points(self.user_id)
        topic_display = _get_topic_display(self.user_id)
        topic_name = topic_display.get(self.topic, self.topic)

        drill_ctx = get_topic_context_for_drill(self.topic, self.user_id)

        due_reviews = get_due_reviews(self.user_id, self.topic)
        due_points = [wp["point"] for wp in due_reviews[:5]]

        all_weak = list(drill_ctx["weak_points"])
        for dp in due_points:
            if dp not in all_weak:
                all_weak.insert(0, dp)

        # Phase 7b: detect cold start so _stage_generate can use a shorter
        # diagnostic prompt and skip personalization injection.
        is_cold_start = (
            not all_weak
            and drill_ctx.get("mastery_score", 0) <= 0
            and not drill_ctx.get("past_insights")
        )

        # Phase 2: build the slot plan from per-WP mastery — but skip it on
        # cold-start. Without weak_points the plan is empty and the validator
        # would flag every question as not covering any WP, triggering pointless
        # repair churn.
        plan = None
        if not is_cold_start:
            profile = _load_profile(self.user_id)
            active_wp_dicts = [
                wp for wp in profile.get("weak_points", [])
                if not wp.get("improved") and wp.get("topic") == self.topic
            ]
            plan = allocate_slots(active_wp_dicts, due_points=set(due_points))

        self.ctx = {
            "topic_name": topic_name,
            "drill_ctx": drill_ctx,
            "due_points": due_points,
            "all_weak": all_weak,
            "plan": plan,
            "is_cold_start": is_cold_start,
        }
        # Keep the legacy "progress" string event so old clients still display
        # something sensible during transition.
        yield sse_event({"type": "progress", "message": "正在准备知识库..."})

    # ── Stage 2: retrieve ──

    async def _stage_retrieve(self) -> AsyncGenerator[str, None]:
        yield sse_event({"type": "progress", "message": "正在检索知识库..."})

        all_weak = self.ctx.get("all_weak", [])
        topic_name = self.ctx["topic_name"]
        fallback_query = self._fallback_query(topic_name)

        # Phase 7a: cache the assembled knowledge_ctx by (topic, weak_points)
        # tuple. Saves the entire RAG hop on repeat training sessions with the
        # same active weak_points (common during a single sitting).
        cache = get_cache()
        cache_key = self._knowledge_cache_key(all_weak)
        cached_payload = await asyncio.to_thread(cache.get_json, cache_key)
        if cached_payload and isinstance(cached_payload, dict):
            self.ctx["knowledge_ctx"] = cached_payload.get("knowledge_ctx", "")
            self.ctx["knowledge_chunks"] = cached_payload.get("chunks", 0)
            self.ctx["retrieval_queries"] = cached_payload.get("queries", 0)
            self.ctx["retrieval_stats"] = None
            self.ctx["knowledge_cache_hit"] = True
            return

        try:
            # Hard end-to-end budget for the whole RAG hop (retrieve + dedup +
            # rerank). This sits on the SSE question-gen path, so cap it well
            # below the worst-case sum of per-stage timeouts and degrade to empty
            # context on overrun rather than making the user wait minutes.
            chunks, stats = await asyncio.wait_for(
                retrieve_for_drill(
                    topic=self.topic,
                    user_id=self.user_id,
                    weak_points=all_weak,
                    fallback_query=fallback_query,
                ),
                timeout=100.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Phase 3 RAG exceeded 100s budget; continuing with empty context")
            chunks, stats = [], None
        except Exception as exc:
            logger.warning("Phase 3 RAG failed (%s); falling back to empty context", exc)
            chunks, stats = [], None

        knowledge_ctx = "\n\n---\n\n".join(chunks)[:5000]
        self.ctx["knowledge_ctx"] = knowledge_ctx
        self.ctx["knowledge_chunks"] = len(chunks)
        self.ctx["retrieval_queries"] = stats.queries if stats else 0
        self.ctx["retrieval_stats"] = stats
        self.ctx["knowledge_cache_hit"] = False

        # Cache TTL 1h — long enough to span a multi-drill sitting but short
        # enough that knowledge-base edits propagate the same day.
        await asyncio.to_thread(cache.set_json, cache_key, {
            "knowledge_ctx": knowledge_ctx,
            "chunks": len(chunks),
            "queries": stats.queries if stats else 0,
        }, 3600)

    def _knowledge_cache_key(self, weak_points: list[str]) -> str:
        import hashlib
        wps_sig = "|".join(sorted(weak_points[:5]))
        digest = hashlib.sha256(f"{self.topic}|{self.user_id}|{wps_sig}".encode("utf-8")).hexdigest()[:16]
        return f"drill:knowledge_ctx:{digest}"

    def _fallback_query(self, topic_name: str) -> str:
        """Build the exploration-style query when weak_points is sparse.

        Phase 3 still uses the generic phrasing as a fallback. Phase 3's
        offline sub-topic JSON (data/topic_subtopics.json) is read on demand;
        when present, we pick 3 random sub-topics instead of the hard-coded
        phrase to inject more semantic surface area.
        """
        try:
            import json
            from backend.config import settings
            sub_path = settings.base_dir / "data" / "topic_subtopics.json"
            if sub_path.exists():
                data = json.loads(sub_path.read_text(encoding="utf-8"))
                sub_topics = data.get(self.topic) or []
                if sub_topics:
                    return " ".join(sub_topics[:3])
        except Exception:
            pass
        return f"{topic_name} 核心知识点 面试常见问题"

    def _rag_quality_hint(self) -> str:
        """Tell the LLM how much to lean on knowledge_context this round.

        Hints align the model's behavior with the actual retrieval outcome:
        - 0 chunks  → don't pretend a reference exists
        - <3 chunks → use sparingly, don't quote
        - ≥3 chunks → ok to anchor depth but never copy verbatim
        """
        chunks = self.ctx.get("knowledge_chunks", 0)
        if not self.ctx.get("knowledge_ctx") or chunks == 0:
            return (
                "⚠️ 本次知识库未召回任何相关内容。"
                "请凭你的领域常识自主出题，**不要**在题目里引用知识库、"
                "也不要使用「参考资料中提到」这类措辞。"
            )
        if chunks < 3:
            return (
                f"ℹ️ 知识库召回稀疏（仅 {chunks} 段），仅供辅助判断深度。"
                "可适当超出召回内容出题，但不要把这几段当作题面来源。"
            )
        return (
            f"✓ 知识库召回 {chunks} 段相关内容，可用于把握技术深度边界。"
            "出题角度仍需独立设计，禁止照搬原文。"
        )

    # ── Stage 3: generate ──

    async def _stage_generate(self) -> AsyncGenerator[str, None]:
        yield sse_event({"type": "progress", "message": "AI 正在生成题目..."})

        # Phase 7b: cold-start uses a dedicated 1.5k-char diagnostic prompt
        # instead of the 5k-char personalized one. The personalized prompt
        # provides little value when every field is "暂无".
        if self.ctx.get("is_cold_start"):
            async for ev in self._generate_cold_start():
                yield ev
            return

        drill_ctx = self.ctx["drill_ctx"]
        all_weak = self.ctx["all_weak"]
        due_points = self.ctx["due_points"]
        topic_name = self.ctx["topic_name"]
        plan = self.ctx.get("plan")

        # Phase 7c: if the topic has a seed pool, draft up to 6 questions
        # from it (matched against active weak_points) and only ask the LLM
        # for the remaining slots. Falls back to all-LLM when no pool exists.
        seed_questions: list[dict] = []
        if has_pool(self.topic):
            recent = drill_ctx.get("recent_questions", [])
            seed_questions = draft_from_seed_pool(
                self.topic, all_weak, n=6, avoid_recent=recent,
            )
        n_from_llm = max(2, 10 - len(seed_questions))
        self.ctx["seed_count"] = len(seed_questions)

        # Phase 7c: emit seed questions up-front so the user sees instant
        # progress while the LLM still has to generate the remainder.
        if seed_questions:
            for q in seed_questions:
                yield sse_event({"type": "question", "data": q})

        past_insights_text = "\n".join(
            f"- {ins[:200]}" for ins in drill_ctx.get("past_insights", [])
        ) or "暂无历史数据"
        high_freq = _load_high_freq(self.topic, self.user_id) or "暂无"

        weak_lines: list[str] = []
        for w in all_weak[:10]:
            prefix = "[到期复习] " if w in due_points else ""
            weak_lines.append(f"- {prefix}{w}")

        # Phase 2: use slot-based strategy when we have a plan with concrete
        # slots; otherwise fall back to the legacy 3-band text (cold start,
        # zero weak_points). Phase 7 will replace this fallback with a
        # dedicated cold-start prompt.
        if plan is not None and any(s.weak_point for s in plan.slots):
            question_strategy = render_strategy_block(plan)
            diff_min, diff_max = difficulty_range_for_plan(plan)
        else:
            mastery_score = drill_ctx["mastery_score"]
            diff_min, diff_max, question_strategy = self._strategy_for_mastery(mastery_score)

        # Phase 7c: if seed pool already covered some slots, tell the LLM
        # to generate just the remaining questions and avoid repeating the
        # seeds' weak_points or wording.
        if seed_questions:
            seed_hint = (
                f"\n\n**重要**：已经有 {len(seed_questions)} 道种子题被加入了本批，"
                f"你只需要再补 **{n_from_llm}** 道题（不是 10 道）。"
                "请避开以下种子题已经覆盖的角度：\n"
                + "\n".join(f"- {q['question'][:80]}" for q in seed_questions)
            )
            question_strategy = question_strategy + seed_hint

        prompt = DRILL_QUESTION_GEN_PROMPT.format(
            topic_name=topic_name,
            knowledge_context=self.ctx.get("knowledge_ctx", ""),
            rag_quality_hint=self._rag_quality_hint(),
            user_profile=get_profile_summary_for_drill(self.user_id),
            mastery_info=drill_ctx["mastery_info"],
            weak_points="\n".join(weak_lines) or "暂无",
            high_freq_questions=high_freq,
            recent_questions="\n".join(f"- {q}" for q in drill_ctx["recent_questions"][-10:]) or "暂无",
            past_insights=past_insights_text,
            question_strategy=question_strategy,
            diff_min=diff_min,
            diff_max=diff_max,
        )

        llm = get_langchain_llm()
        accumulated = ""
        emitted_count = 0
        # Seed-question ids occupy 1..len(seed_questions); LLM questions
        # continue from len(seed_questions)+1.
        seed_offset = len(seed_questions)

        async for chunk in llm.astream([
            SystemMessage(content="你是专项训练出题引擎。只返回 JSON 数组，不要其他内容。"),
            HumanMessage(content=prompt),
        ]):
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            accumulated += token

            objects, _ = extract_complete_objects(accumulated)
            while emitted_count < len(objects) and emitted_count < n_from_llm:
                q = objects[emitted_count]
                # Force IDs so seeds + LLM questions form a contiguous 1..10 list.
                q["id"] = seed_offset + emitted_count + 1
                emitted_count += 1
                yield sse_event({"type": "question", "data": q})

        # Fallback: nothing emitted incrementally — try a final non-stream parse.
        if emitted_count == 0:
            try:
                parsed = _parse_json_response(accumulated)
                if isinstance(parsed, list):
                    fixed: list[dict] = []
                    for i, q in enumerate(parsed[:n_from_llm]):
                        q["id"] = seed_offset + i + 1
                        yield sse_event({"type": "question", "data": q})
                        fixed.append(q)
                    self.questions = seed_questions + fixed
                    return
            except Exception:
                raise RuntimeError("出题失败，LLM 返回格式异常")

        all_objects, _ = extract_complete_objects(accumulated)
        llm_questions: list[dict] = []
        for i, q in enumerate(all_objects[:n_from_llm]):
            q["id"] = seed_offset + i + 1
            llm_questions.append(q)
        self.questions = (seed_questions + llm_questions)[:10]

    @staticmethod
    def _strategy_for_mastery(mastery_score: float) -> tuple[int, int, str]:
        """Same 3-band strategy as the legacy code. Phase 2 replaces this."""
        if mastery_score <= 30:
            return 1, 3, (
                "当前为新手阶段（掌握度 0-30），题目策略：\n"
                "- 70% 基础概念题 + 对比辨析题，30% 简单应用题\n"
                "- 概念题要考理解而非背诵——问「为什么这样设计」而非「请背诵定义」"
            )
        if mastery_score <= 60:
            return 2, 4, (
                "当前有基础（掌握度 30-60），题目策略：\n"
                "- 40% 深度概念题，40% 场景应用题，20% 设计权衡题"
            )
        return 3, 5, (
            "当前已熟练（掌握度 60-100），题目策略：\n"
            "- 20% 概念题（考边界 case 和底层原理），80% 场景设计 + 系统权衡题"
        )

    async def _generate_cold_start(self) -> AsyncGenerator[str, None]:
        """Phase 7b: shorter diagnostic prompt for never-trained users."""
        topic_name = self.ctx["topic_name"]
        prompt = COLD_START_DRILL_PROMPT.format(
            topic_name=topic_name,
            knowledge_context=self.ctx.get("knowledge_ctx", "")[:2500],  # 比正常更短
            rag_quality_hint=self._rag_quality_hint(),
        )

        from langchain_core.messages import HumanMessage as _Hum, SystemMessage as _Sys

        llm = get_langchain_llm()
        accumulated = ""
        emitted_count = 0
        async for chunk in llm.astream([
            _Sys(content="你是初次诊断的出题引擎，只返回 JSON 数组。"),
            _Hum(content=prompt),
        ]):
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            accumulated += token
            objects, _ = extract_complete_objects(accumulated)
            while emitted_count < len(objects):
                q = objects[emitted_count]
                if "id" not in q:
                    q["id"] = emitted_count + 1
                emitted_count += 1
                yield sse_event({"type": "question", "data": q})

        all_objects, _ = extract_complete_objects(accumulated)
        if not all_objects:
            try:
                parsed = _parse_json_response(accumulated)
                if isinstance(parsed, list):
                    all_objects = parsed
            except Exception as exc:
                raise RuntimeError(f"冷启动出题失败: {exc}")

        for i, q in enumerate(all_objects):
            if "id" not in q:
                q["id"] = i + 1
        self.questions = all_objects[:10]

    # ── Stage 4: validate ──

    async def _stage_validate(self) -> AsyncGenerator[str, None]:
        """Run structural validators; selectively repair failing questions.

        Repair budget is capped at 1 attempt to bound cost. If the second pass
        still fails, we log warning and let the (possibly imperfect) batch
        through — the eval harness (Phase 6) will catch persistent regressions.
        """
        if not self.questions:
            return

        # Cold start has no weak_points and no slot plan — the validators would
        # flag every question as "doesn't cover any WP" and trigger a useless
        # repair loop. Skip validation entirely on cold start.
        if self.ctx.get("is_cold_start"):
            self.ctx["validator_summary"] = ["cold_start=skipped"]
            return

        plan = self.ctx.get("plan")
        ctx = ValidationContext(
            topic=self.topic,
            user_id=self.user_id,
            target_difficulty_distribution=(plan.difficulty_distribution() if plan else None),
            weak_points=[s.weak_point for s in (plan.slots if plan else []) if s.weak_point]
                        or self.ctx.get("all_weak", []),
            recent_questions=self.ctx.get("drill_ctx", {}).get("recent_questions", []),
        )

        bad_ids: list[int] = []
        reasons: dict[int, str] = {}
        validator_summaries: list[str] = []

        for v in DEFAULT_VALIDATORS:
            try:
                result: ValidationResult = v.validate(self.questions, ctx)
            except Exception as exc:
                logger.warning("Validator %s crashed: %s", v.name, exc)
                continue
            validator_summaries.append(f"{v.name}={'ok' if result.ok else 'fail'}({result.summary})")
            if not result.ok:
                for qid in result.bad_ids:
                    if qid in bad_ids:
                        continue
                    bad_ids.append(qid)
                    reasons[qid] = result.reasons.get(qid, f"{v.name} flagged this question")

        self.ctx["validator_summary"] = validator_summaries

        if not bad_ids:
            return

        yield sse_event({
            "type": "validate_failed",
            "bad_ids": bad_ids,
            "reasons": list(reasons.values()),
        })

        try:
            repaired = await self._repair_partial(bad_ids, reasons)
        except Exception as exc:
            logger.warning("Repair pass failed: %s", exc)
            self.ctx["repair_outcome"] = f"failed: {exc}"
            return

        # Replace the bad questions with the repaired ones, preserving order/IDs.
        # Emit as `question_update` (not `question`) so the frontend replaces
        # in place by id instead of appending a duplicate card.
        repaired_by_id = {q.get("id"): q for q in repaired if q.get("id") in bad_ids}
        for i, q in enumerate(self.questions):
            if q.get("id") in repaired_by_id:
                self.questions[i] = repaired_by_id[q["id"]]
                yield sse_event({"type": "question_update", "data": self.questions[i], "repaired": True})

        self.ctx["repair_outcome"] = f"repaired {len(repaired_by_id)}/{len(bad_ids)}"

    async def _repair_partial(self, bad_ids: list[int], reasons: dict[int, str]) -> list[dict]:
        """Single LLM call that re-emits ONLY the questions listed in bad_ids."""
        bad_lines = []
        for qid in bad_ids:
            original = next((q for q in self.questions if q.get("id") == qid), None)
            if original:
                bad_lines.append(
                    f"- 题号 #{qid}（原题：{original.get('question', '')[:120]} | 原难度 {original.get('difficulty', '?')})\n"
                    f"  反馈：{reasons.get(qid, '需修复')}"
                )

        original_q_text = "\n".join(
            f"#{q.get('id')} [难度 {q.get('difficulty', '?')}/5] {q.get('question', '')[:150]}"
            for q in self.questions
        )

        drill_ctx = self.ctx["drill_ctx"]
        weak_lines = "\n".join(f"- {w}" for w in self.ctx.get("all_weak", [])[:8]) or "暂无"

        prompt = DRILL_REPAIR_PROMPT.format(
            topic_name=self.ctx["topic_name"],
            original_questions=original_q_text,
            bad_questions="\n".join(bad_lines),
            user_profile=get_profile_summary_for_drill(self.user_id),
            mastery_info=drill_ctx["mastery_info"],
            weak_points=weak_lines,
        )

        from langchain_core.messages import HumanMessage as _Hum, SystemMessage as _Sys

        llm = get_langchain_llm()
        response = await llm.ainvoke([
            _Sys(content="你是出题修复引擎。只返回 JSON 数组，长度严格等于需要重出的题数。"),
            _Hum(content=prompt),
        ])
        raw = response.content if hasattr(response, "content") else str(response)
        parsed = _parse_json_response(raw)
        if not isinstance(parsed, list):
            raise RuntimeError(f"Repair returned non-list: {type(parsed).__name__}")
        # Force the IDs back onto the bad slots in order so we can map them.
        for i, q in enumerate(parsed):
            if i < len(bad_ids):
                q["id"] = bad_ids[i]
        return parsed

    # ── Stage 5: finalize ──

    async def _stage_finalize(self) -> AsyncGenerator[str, None]:
        # Phase 5B: calibrate LLM-self-reported difficulty by k-NN against
        # difficulty anchors. Best-effort — no-op when anchors aren't loaded.
        # calibrate_difficulties does sync embeddings; offload to a thread so
        # we don't stall the event loop while the SSE response is open.
        try:
            from backend.graphs.difficulty_anchors import calibrate_difficulties
            calibrated, total = await asyncio.to_thread(
                calibrate_difficulties, self.topic, self.questions,
            )
            self.ctx["difficulty_calibrated"] = (calibrated, total)
            if calibrated:
                # Re-emit updated questions so the client sees the new difficulty.
                for q in self.questions:
                    if "difficulty_llm" in q:
                        yield sse_event({"type": "question_update", "data": q})
        except Exception as exc:
            logger.warning("Difficulty calibration skipped: %s", exc)

        self.session_id = uuid.uuid4().hex[:8]
        create_session(
            self.session_id, self.mode, self.topic,
            questions=self.questions, user_id=self.user_id,
        )
        save_live(drill_sessions, self.session_id, "drill", self.user_id, {
            "topic": self.topic,
            "questions": self.questions,
            "user_id": self.user_id,
        })
        yield sse_event({
            "type": "done",
            "session_id": self.session_id,
            "topic": self.topic,
            "mode": self.mode,
            "total": len(self.questions),
        })
