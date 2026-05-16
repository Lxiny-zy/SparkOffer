"""Interview routes — start, chat, end for drill / resume / JD-prep modes."""
import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from backend.models import (
    StartInterviewRequest, ChatRequest, EndDrillRequest,
    InterviewMode, InterviewPhase,
)
from backend.config import settings
from backend.indexer import load_topics
from backend.memory import update_profile_after_interview, llm_update_profile
from backend.storage.sessions import (
    create_session, append_message, save_review, save_drill_answers,
)
from backend.graphs.resume_interview import compile_resume_interview
from backend.graphs.topic_drill import generate_drill_questions, evaluate_drill_answers, stream_evaluate_drill_answers
from backend.graphs.job_prep import evaluate_job_prep_answers, stream_evaluate_job_prep_answers
from backend.graphs.review import generate_review, stream_generate_review
from backend.formatters import format_drill_review, format_job_prep_review
from backend.live_store import (
    graphs, drill_sessions, job_prep_sessions,
    save_live, get_live, del_live,
)
from backend.auth import get_current_user

router = APIRouter(prefix="/api")


@router.post("/interview/start")
async def start_interview(req: StartInterviewRequest, user_id: str = Depends(get_current_user)):
    session_id = str(uuid.uuid4())[:8]

    if req.mode == InterviewMode.TOPIC_DRILL:
        topics = await asyncio.to_thread(load_topics, user_id)
        if not req.topic or req.topic not in topics:
            raise HTTPException(400, f"Invalid topic. Available: {list(topics.keys())}")
        try:
            questions = await asyncio.to_thread(generate_drill_questions, req.topic, user_id)
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        create_session(session_id, req.mode.value, req.topic, questions=questions, user_id=user_id)
        save_live(drill_sessions, session_id, "drill", user_id, {"topic": req.topic, "questions": questions, "user_id": user_id})
        return {
            "session_id": session_id,
            "mode": req.mode.value,
            "topic": req.topic,
            "questions": questions,
        }

    if req.mode == InterviewMode.RESUME:
        from backend.utils.sse_helpers import stream_blocking_sse, streaming_response, sse_event

        graph = compile_resume_interview(user_id)
        initial_state = {}
        config = {"configurable": {"thread_id": session_id}}

        async def _gen():
            try:
                async for kind, value in stream_blocking_sse(
                    graph.invoke, initial_state, config,
                    progress_msg="正在准备面试",
                ):
                    if kind == "sse":
                        yield value
                    else:
                        result = value

                ai_message = ""
                for msg in reversed(result["messages"]):
                    if isinstance(msg, AIMessage):
                        ai_message = msg.content
                        break

                create_session(session_id, req.mode.value, req.topic, user_id=user_id)
                append_message(session_id, "assistant", ai_message, user_id=user_id)
                graphs[session_id] = {
                    "graph": graph, "config": config,
                    "mode": req.mode, "topic": req.topic,
                    "user_id": user_id,
                }
                yield sse_event({"type": "complete", "data": {
                    "session_id": session_id, "mode": req.mode.value,
                    "topic": req.topic, "message": ai_message,
                }})
                yield sse_event({"type": "done"})
            except Exception as e:
                import logging as _log
                _log.getLogger("uvicorn").error(f"Resume interview SSE error: {e}")
                yield sse_event({"type": "error", "message": f"面试初始化失败: {str(e)[:200]}"})

        return streaming_response(_gen())

    raise HTTPException(400, f"Unsupported mode for this endpoint: {req.mode.value}")


@router.post("/interview/start-stream")
async def start_interview_stream(req: StartInterviewRequest, user_id: str = Depends(get_current_user)):
    if req.mode != InterviewMode.TOPIC_DRILL:
        raise HTTPException(400, "Streaming is only supported for topic_drill mode.")

    topics = load_topics(user_id)
    if not req.topic or req.topic not in topics:
        raise HTTPException(400, f"Invalid topic. Available: {list(topics.keys())}")

    async def stream_questions():
        from backend.graphs.topic_drill import (
            _get_topic_display, _load_high_freq, _parse_json_response,
        )
        from backend.memory import get_topic_context_for_drill, get_profile_summary_for_drill
        from backend.indexer import safe_retrieve_topic_context
        from backend.spaced_repetition import get_due_reviews, init_sr_for_existing_points
        from backend.llm_provider import get_langchain_llm
        from backend.prompts.interviewer import DRILL_QUESTION_GEN_PROMPT

        # 立即发送进度事件，防止代理层因首包超时返回 524
        yield f"data: {json.dumps({'type': 'progress', 'message': '正在准备知识库...'}, ensure_ascii=False)}\n\n"

        init_sr_for_existing_points(user_id)
        topic_display = _get_topic_display(user_id)
        topic_name = topic_display.get(req.topic, req.topic)
        drill_ctx = get_topic_context_for_drill(req.topic, user_id)

        due_reviews = get_due_reviews(user_id, req.topic)
        due_points = [wp["point"] for wp in due_reviews[:5]]
        all_weak = list(drill_ctx["weak_points"])
        for dp in due_points:
            if dp not in all_weak:
                all_weak.insert(0, dp)

        yield f"data: {json.dumps({'type': 'progress', 'message': '正在检索知识库...'}, ensure_ascii=False)}\n\n"

        queries = []
        if all_weak:
            queries.append(" ".join(all_weak[:5]))
        queries.append(f"{topic_name} 核心知识点 面试常见问题")
        all_chunks = []
        for q in queries:
            chunks = await safe_retrieve_topic_context(req.topic, q, user_id, top_k=5, timeout=60.0)
            if chunks:
                all_chunks.extend(chunks)
            else:
                yield f"data: {json.dumps({'type': 'progress', 'message': '知识库检索超时或失败，跳过部分知识...'}, ensure_ascii=False)}\n\n"
        seen = set()
        unique_chunks = []
        for c in all_chunks:
            key = c[:100]
            if key not in seen:
                seen.add(key)
                unique_chunks.append(c)
        knowledge_ctx = "\n\n---\n\n".join(unique_chunks)[:5000]

        past_insights_text = "\n".join(
            f"- {ins[:200]}" for ins in drill_ctx.get("past_insights", [])
        ) or "暂无历史数据"
        high_freq = _load_high_freq(req.topic, user_id) or "暂无"

        weak_lines = []
        for w in all_weak[:10]:
            prefix = "[到期复习] " if w in due_points else ""
            weak_lines.append(f"- {prefix}{w}")

        mastery_score = drill_ctx["mastery_score"]
        if mastery_score <= 30:
            diff_min, diff_max = 1, 3
            question_strategy = (
                "当前为新手阶段（掌握度 0-30），题目策略：\n"
                "- 70% 基础概念题 + 对比辨析题，30% 简单应用题\n"
                "- 概念题要考理解而非背诵——问「为什么这样设计」而非「请背诵定义」"
            )
        elif mastery_score <= 60:
            diff_min, diff_max = 2, 4
            question_strategy = (
                "当前有基础（掌握度 30-60），题目策略：\n"
                "- 40% 深度概念题，40% 场景应用题，20% 设计权衡题"
            )
        else:
            diff_min, diff_max = 3, 5
            question_strategy = (
                "当前已熟练（掌握度 60-100），题目策略：\n"
                "- 20% 概念题（考边界 case 和底层原理），80% 场景设计 + 系统权衡题"
            )

        prompt = DRILL_QUESTION_GEN_PROMPT.format(
            topic_name=topic_name,
            knowledge_context=knowledge_ctx,
            user_profile=get_profile_summary_for_drill(user_id),
            mastery_info=drill_ctx["mastery_info"],
            weak_points="\n".join(weak_lines) or "暂无",
            high_freq_questions=high_freq,
            recent_questions="\n".join(f"- {q}" for q in drill_ctx["recent_questions"][-10:]) or "暂无",
            past_insights=past_insights_text,
            question_strategy=question_strategy,
            diff_min=diff_min,
            diff_max=diff_max,
        )

        yield f"data: {json.dumps({'type': 'progress', 'message': 'AI 正在生成题目...'}, ensure_ascii=False)}\n\n"

        llm = get_langchain_llm()
        from backend.utils.stream_parser import extract_complete_objects

        accumulated = ""
        emitted_count = 0

        async for chunk in llm.astream([
            SystemMessage(content="你是专项训练出题引擎。只返回 JSON 数组，不要其他内容。"),
            HumanMessage(content=prompt),
        ]):
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            accumulated += token

            objects, _ = extract_complete_objects(accumulated)
            while emitted_count < len(objects):
                q = objects[emitted_count]
                if "id" not in q:
                    q["id"] = emitted_count + 1
                emitted_count += 1
                yield f"data: {json.dumps({'type': 'question', 'data': q}, ensure_ascii=False)}\n\n"

        if emitted_count == 0:
            try:
                questions = _parse_json_response(accumulated)
                if isinstance(questions, list):
                    for i, q in enumerate(questions[:10]):
                        if "id" not in q:
                            q["id"] = i + 1
                        yield f"data: {json.dumps({'type': 'question', 'data': q}, ensure_ascii=False)}\n\n"
                    emitted_count = len(questions[:10])
            except Exception:
                yield f"data: {json.dumps({'type': 'error', 'message': '出题失败，请重试'})}\n\n"
                return

        session_id = str(uuid.uuid4())[:8]
        all_questions, _ = extract_complete_objects(accumulated)
        for i, q in enumerate(all_questions):
            if "id" not in q:
                q["id"] = i + 1
        questions = all_questions[:10]

        create_session(session_id, req.mode.value, req.topic, questions=questions, user_id=user_id)
        save_live(drill_sessions, session_id, "drill", user_id, {
            "topic": req.topic, "questions": questions, "user_id": user_id,
        })

        yield f"data: {json.dumps({'type': 'done', 'session_id': session_id, 'topic': req.topic, 'mode': req.mode.value, 'total': len(questions)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        stream_questions(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/interview/chat")
async def chat(req: ChatRequest, user_id: str = Depends(get_current_user)):
    if req.session_id not in graphs:
        raise HTTPException(404, "Session not found. It may have expired (in-memory only).")

    entry = graphs[req.session_id]
    if entry.get("user_id") != user_id:
        raise HTTPException(403, "Access denied.")

    graph = entry["graph"]
    config = entry["config"]

    from backend.utils.sse_helpers import stream_blocking_sse, streaming_response, sse_event

    graph.update_state(config, {"messages": [HumanMessage(content=req.message)]})

    async def _gen():
        async for kind, value in stream_blocking_sse(
            graph.invoke, None, config,
            progress_msg="面试官正在思考",
        ):
            if kind == "sse":
                yield value
            else:
                result = value

        append_message(req.session_id, "user", req.message, user_id=user_id)

        is_finished = False
        if isinstance(result, dict):
            is_finished = result.get("is_finished", False)
            phase = result.get("phase", "")
            if phase in (InterviewPhase.END.value, "end"):
                is_finished = True

        ai_message = ""
        for msg in reversed(result["messages"]):
            if isinstance(msg, AIMessage):
                ai_message = msg.content
                break

        append_message(req.session_id, "assistant", ai_message, user_id=user_id)
        yield sse_event({"type": "complete", "data": {
            "session_id": req.session_id, "message": ai_message, "is_finished": is_finished,
        }})
        yield sse_event({"type": "done"})

    return streaming_response(_gen())


@router.post("/interview/end/{session_id}")
async def end_interview(session_id: str, body: EndDrillRequest = None,
                        user_id: str = Depends(get_current_user)):
    # -- Drill mode --
    entry = get_live(drill_sessions, session_id, "drill")
    if entry:
        if entry.get("user_id") != user_id:
            raise HTTPException(403, "Access denied.")

        topic = entry["topic"]
        questions = entry["questions"]
        answers = body.answers if body and body.answers else []
        save_drill_answers(session_id, answers, user_id=user_id)

        async def _stream_drill():
            eval_result = {}
            async for sse_line in stream_evaluate_drill_answers(topic, questions, answers, user_id):
                yield sse_line
                if sse_line.startswith("data: "):
                    try:
                        evt = json.loads(sse_line[6:].strip())
                        if evt.get("type") == "eval_result":
                            eval_result = evt["data"]
                    except (json.JSONDecodeError, KeyError):
                        pass

            scores = eval_result.get("scores", [])
            overall = eval_result.get("overall", {})

            q_diff = {q["id"]: q.get("difficulty", 3) for q in questions}
            for s in scores:
                s.setdefault("difficulty", q_diff.get(s.get("question_id"), 3))

            review = format_drill_review(questions, answers, scores, overall)
            save_review(session_id, review, scores, overall.get("new_weak_points", []), overall, user_id=user_id)

            from backend.spaced_repetition import update_weak_point_sr
            for s in scores:
                wp = s.get("weak_point")
                sc = s.get("score")
                if wp and isinstance(sc, (int, float)):
                    update_weak_point_sr(topic, wp, sc, user_id)

            await _update_drill_profile(topic, overall, scores, len(questions), user_id)
            del_live(drill_sessions, session_id)

            try:
                from backend.knowledge_evolution import extract_and_writeback, collect_high_freq
                await extract_and_writeback(topic, questions, answers, scores, user_id)
                await collect_high_freq(topic, questions, scores, user_id)
            except Exception as e:
                import logging
                logging.getLogger("uvicorn").warning(f"Knowledge evolution failed: {e}")

            result = {
                "session_id": session_id,
                "mode": "topic_drill",
                "review": review,
                "scores": scores,
                "overall": overall,
            }
            yield f"data: {json.dumps({'type': 'complete', 'data': result}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(
            _stream_drill(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- JD prep mode --
    entry = get_live(job_prep_sessions, session_id, "job_prep")
    if entry:
        if entry.get("user_id") != user_id:
            raise HTTPException(403, "Access denied.")

        questions = entry["questions"]
        preview = entry["preview"]
        meta = entry["meta"]
        answers = body.answers if body and body.answers else []
        save_drill_answers(session_id, answers, user_id=user_id)

        async def _stream_job_prep():
            eval_result = {}
            async for sse_line in stream_evaluate_job_prep_answers(questions, answers, preview, user_id):
                yield sse_line
                if sse_line.startswith("data: "):
                    try:
                        evt = json.loads(sse_line[6:].strip())
                        if evt.get("type") == "eval_result":
                            eval_result = evt["data"]
                    except (json.JSONDecodeError, KeyError):
                        pass

            scores = eval_result.get("scores", [])
            overall = eval_result.get("overall", {})

            q_diff = {q["id"]: q.get("difficulty", 3) for q in questions}
            for s in scores:
                s.setdefault("difficulty", q_diff.get(s.get("question_id"), 3))

            review = format_job_prep_review(questions, answers, scores, overall, meta)
            save_review(session_id, review, scores, overall.get("new_weak_points", []), overall, user_id=user_id)

            await _update_job_prep_profile(overall, scores, len(questions), meta, user_id)
            del_live(job_prep_sessions, session_id)

            try:
                from backend.knowledge_evolution import extract_and_writeback, collect_high_freq
                jd_topics = _match_jd_to_topics(meta, user_id)
                for t in jd_topics:
                    await extract_and_writeback(t, questions, answers, scores, user_id)
                    await collect_high_freq(t, questions, scores, user_id)
            except Exception as e:
                import logging
                logging.getLogger("uvicorn").warning(f"JD prep knowledge evolution failed: {e}")

            result = {
                "session_id": session_id,
                "mode": InterviewMode.JD_PREP.value,
                "review": review,
                "scores": scores,
                "overall": overall,
                "meta": meta,
                "position": meta.get("position"),
                "company": meta.get("company"),
            }
            yield f"data: {json.dumps({'type': 'complete', 'data': result}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(
            _stream_job_prep(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- Resume mode --
    if session_id not in graphs:
        raise HTTPException(404, "Session not found.")

    entry = graphs[session_id]
    if entry.get("user_id") != user_id:
        raise HTTPException(403, "Access denied.")

    graph = entry["graph"]
    config = entry["config"]

    state = graph.get_state(config)
    messages = state.values.get("messages", [])
    scores = state.values.get("scores", [])
    weak_points = state.values.get("weak_points", [])
    eval_history = state.values.get("eval_history", [])
    topic_name = state.values.get("topic_name", entry.get("topic"))

    async def _stream_resume():
        yield f"data: {json.dumps({'type': 'eval_start', 'total': len(messages)}, ensure_ascii=False)}\n\n"

        review_text = ""
        async for sse_line in stream_generate_review(
            mode=entry["mode"],
            messages=messages,
            scores=scores,
            weak_points=weak_points,
            topic=topic_name,
            eval_history=eval_history,
        ):
            yield sse_line
            if sse_line.startswith("data: "):
                try:
                    evt = json.loads(sse_line[6:].strip())
                    if evt.get("type") == "review_result":
                        review_text = evt["data"]
                except (json.JSONDecodeError, KeyError):
                    pass

        extraction = await update_profile_after_interview(
            mode=entry["mode"].value,
            topic=entry.get("topic"),
            messages=messages,
            user_id=user_id,
            scores=scores,
        )

        resume_overall = {}
        if extraction.get("dimension_scores"):
            resume_overall["dimension_scores"] = extraction["dimension_scores"]
        if extraction.get("avg_score"):
            resume_overall["avg_score"] = extraction["avg_score"]
        save_review(session_id, review_text, scores, weak_points, overall=resume_overall, user_id=user_id)

        del_live(graphs, session_id)

        try:
            from backend.knowledge_evolution import extract_and_writeback, collect_high_freq
            resume_topics = _match_resume_to_topics(messages, user_id)
            if resume_topics and eval_history:
                resume_qs = [{"question": e.get("question", "")} for e in eval_history if e.get("question")]
                resume_scores = [{"score": e.get("score", 5), "assessment": e.get("assessment", "")} for e in eval_history]
                resume_answers = [e.get("answer", "") for e in eval_history]
                for t in resume_topics:
                    await extract_and_writeback(t, resume_qs, resume_answers, resume_scores, user_id)
                    await collect_high_freq(t, resume_qs, resume_scores, user_id)
        except Exception as e:
            import logging
            logging.getLogger("uvicorn").warning(f"Resume knowledge evolution failed: {e}")

        result = {
            "session_id": session_id,
            "mode": "resume",
            "review": review_text,
            "profile_update": {
                "new_weak_points": extraction.get("weak_points", []),
                "new_strong_points": extraction.get("strong_points", []),
                "session_summary": extraction.get("session_summary", ""),
            },
            "dimension_scores": extraction.get("dimension_scores"),
            "avg_score": extraction.get("avg_score"),
        }
        yield f"data: {json.dumps({'type': 'complete', 'data': result}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        _stream_resume(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/interview/reference-answer")
async def generate_reference_answer(body: dict, user_id: str = Depends(get_current_user)):
    topic = body.get("topic", "").strip()
    question = body.get("question", "").strip()
    session_id = body.get("session_id")
    question_id = body.get("question_id")
    force = body.get("force", False)
    mode = body.get("mode", "full")
    if not topic or not question:
        raise HTTPException(400, "topic and question are required")

    from backend.storage.sessions import get_reference_answer, save_reference_answer

    cache_key_suffix = f"_hint" if mode == "hint" else ""
    cache_qid = f"{question_id}{cache_key_suffix}" if question_id else None

    if session_id and cache_qid and not force:
        cached = get_reference_answer(session_id, cache_qid, user_id=user_id)
        if cached:
            return {"reference_answer": cached, "cached": True, "mode": mode}

    from backend.indexer import safe_retrieve_topic_context
    from backend.llm_provider import get_langchain_llm
    from backend.prompts.interviewer import REFERENCE_ANSWER_PROMPT, HINT_PROMPT

    topics = load_topics(user_id)
    topic_name = topics.get(topic, {}).get("name", topic)

    refs = await safe_retrieve_topic_context(topic, question, user_id, top_k=3, timeout=60.0)
    knowledge_context = "\n\n".join(refs) if refs else "（暂无参考材料）"

    if mode == "hint":
        prompt = HINT_PROMPT.format(
            topic_name=topic_name, question=question, knowledge_context=knowledge_context,
        )
    else:
        prompt = REFERENCE_ANSWER_PROMPT.format(
            topic_name=topic_name, question=question, knowledge_context=knowledge_context,
        )

    from backend.utils.sse_helpers import stream_llm_sse, streaming_response, sse_event

    lc_messages = [HumanMessage(content=prompt)]

    async def _gen():
        answer = ""
        async for kind, value in stream_llm_sse(lc_messages, progress_prefix="正在生成参考答案"):
            if kind == "sse":
                yield value
            else:
                answer = value.strip()

        if session_id and cache_qid:
            save_reference_answer(session_id, cache_qid, answer, user_id=user_id)

        yield sse_event({"type": "complete", "data": {"reference_answer": answer, "cached": False, "mode": mode}})
        yield sse_event({"type": "done"})

    return streaming_response(_gen())


# ── Topic matching helpers ──

def _match_resume_to_topics(messages: list, user_id: str) -> list[str]:
    """Infer relevant knowledge topics from resume interview messages."""
    topics = load_topics(user_id)
    if not topics:
        return []
    text = " ".join(
        (m.content if hasattr(m, "content") else str(m))[:200]
        for m in messages[-20:]
    ).lower()
    matched = []
    for key, info in topics.items():
        name = info.get("name", key).lower()
        if name in text or key.lower() in text:
            matched.append(key)
    return matched[:3]


def _match_jd_to_topics(meta: dict, user_id: str) -> list[str]:
    """Match JD prep metadata to user's knowledge topics."""
    topics = load_topics(user_id)
    if not topics:
        return []
    jd_text = (meta.get("jd_text", "") + " " + meta.get("position", "")).lower()
    matched = []
    for key, info in topics.items():
        name = info.get("name", key).lower()
        if name in jd_text or key.lower() in jd_text:
            matched.append(key)
    return matched[:3]


# ── Profile update helpers ──

async def _update_drill_profile(topic: str, overall: dict, scores: list,
                                total_questions: int, user_id: str):
    valid = []
    for s in scores:
        try:
            valid.append((float(s["score"]), float(s.get("difficulty", 3))))
        except (TypeError, ValueError, KeyError):
            pass
    mastery = overall.get("topic_mastery", {})
    coverage = len(valid) / total_questions if total_questions else 0
    session_weight = coverage * 0.4

    if valid:
        contributions = [(d / 5) * (s / 10) for s, d in valid]
        mastery["score"] = round(sum(contributions) / total_questions * 100, 1)
    mastery.pop("level", None)

    await llm_update_profile(
        mode="topic_drill", topic=topic,
        new_weak_points=overall.get("new_weak_points", []),
        new_strong_points=overall.get("new_strong_points", []),
        topic_mastery=mastery,
        communication=overall.get("communication_observations", {}),
        user_id=user_id,
        thinking_patterns=overall.get("thinking_patterns"),
        session_summary=overall.get("summary", ""),
        avg_score=overall.get("avg_score"),
        answer_count=len(scores),
        session_weight=session_weight,
    )


async def _update_job_prep_profile(overall: dict, scores: list, total_questions: int,
                                   meta: dict, user_id: str):
    valid = []
    for s in scores:
        try:
            valid.append(float(s["score"]))
        except (TypeError, ValueError, KeyError):
            pass

    topic = meta.get("position") or "JD 备面"
    coverage = len(valid) / total_questions if total_questions else 0
    session_weight = max(0.25, coverage * 0.5)
    summary = overall.get("summary", "")
    role_fit = overall.get("role_fit_summary", "")
    if role_fit:
        summary = f"{summary}\n\n岗位匹配度判断: {role_fit}".strip()

    await llm_update_profile(
        mode="jd_prep", topic=topic,
        new_weak_points=overall.get("new_weak_points", []),
        new_strong_points=overall.get("new_strong_points", []),
        topic_mastery={},
        communication=overall.get("communication_observations", {}),
        user_id=user_id,
        thinking_patterns=overall.get("thinking_patterns"),
        session_summary=summary,
        avg_score=overall.get("avg_score"),
        answer_count=len(valid),
        session_weight=session_weight,
        dimension_scores=overall.get("dimension_scores"),
    )
