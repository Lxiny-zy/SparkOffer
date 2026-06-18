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
    create_session, append_message, save_review, save_drill_answers, get_session,
    mark_session_synced,
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
from backend.storage.live_sessions import save_live_session, load_live_session
from backend.utils.sse_helpers import sse_event, streaming_response
from backend.auth import get_current_user

router = APIRouter(prefix="/api")


def _answers_from_transcript(questions: list, transcript: list) -> list:
    """Rebuild ``[{question_id, answer}]`` from a persisted drill transcript.

    ``save_drill_answers`` writes the transcript as question/answer pairs in
    question order (assistant=question, then an optional user=answer). This
    recovers the originally-submitted answers when re-evaluating a session whose
    live entry is gone (completed eval, restart, TTL eviction).
    """
    text_to_answer: dict[str, str] = {}
    msgs = transcript or []
    i = 0
    while i < len(msgs):
        m = msgs[i]
        if m.get("role") == "assistant":
            ans = ""
            if i + 1 < len(msgs) and msgs[i + 1].get("role") == "user":
                ans = msgs[i + 1].get("content", "") or ""
                i += 1
            text_to_answer[m.get("content", "")] = ans
        i += 1
    return [
        {"question_id": q["id"], "answer": text_to_answer.get(q.get("question", ""), "")}
        for q in questions
    ]


def _resolve_answers(body, existing: dict | None, questions: list) -> list:
    """Pick the richer answer source: the request body vs the persisted transcript.

    On a first submit the body carries the answers. On re-evaluation the live
    answers may be gone or the restored progress stale, so the transcript is the
    authoritative record. Whichever has more non-empty answers wins.
    """
    body_answers = list(body.answers) if (body and body.answers) else []
    body_filled = sum(1 for a in body_answers if str(a.get("answer", "")).strip())
    transcript_answers = _answers_from_transcript(questions, (existing or {}).get("transcript", []))
    transcript_filled = sum(1 for a in transcript_answers if a["answer"].strip())
    if transcript_filled > body_filled:
        return transcript_answers
    return body_answers if body_answers else transcript_answers


def _get_resume_graph(session_id: str, user_id: str) -> dict | None:
    """Return the in-memory resume-graph entry, rehydrating on a cache miss.

    The graph object can't be persisted, but its state lives in the SqliteSaver
    keyed by ``thread_id=session_id``. On a miss (restart / different worker /
    TTL eviction) we reload the session meta from ``live_sessions``, recompile
    the graph bound to the same user, and the checkpoint replays automatically
    on the next ``invoke``/``get_state``. Returns None when no such session
    exists (caller raises 404).
    """
    if session_id in graphs:
        return graphs[session_id]
    meta = load_live_session(session_id, user_id)
    if not meta:
        return None
    entry = {
        "graph": compile_resume_interview(meta["user_id"]),
        "config": {"configurable": {"thread_id": session_id}},
        "mode": InterviewMode(meta.get("mode", InterviewMode.RESUME.value)),
        "topic": meta.get("topic"),
        "user_id": meta["user_id"],
    }
    graphs[session_id] = entry
    return entry


@router.get("/interview/rag-metrics")
async def get_rag_metrics(
    topic: str = None, stage: str = None,
    limit: int = 50, offset: int = 0,
    user_id: str = Depends(get_current_user),
):
    from backend.storage.rag_metrics_store import get_rag_metrics_history
    return await asyncio.to_thread(
        get_rag_metrics_history, user_id, topic, stage, limit, offset,
    )


@router.get("/interview/rag-metrics/{session_id}")
async def get_session_rag_metrics(
    session_id: str, user_id: str = Depends(get_current_user),
):
    from backend.storage.rag_metrics_store import get_rag_metrics_for_session
    return await asyncio.to_thread(
        get_rag_metrics_for_session, session_id, user_id,
    )


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
                result = None
                async for kind, value in stream_blocking_sse(
                    graph.invoke, initial_state, config,
                    progress_msg="正在准备面试",
                ):
                    if kind == "sse":
                        yield value
                    else:
                        result = value

                if result is None:
                    # graph.invoke failed — stream_blocking_sse already yielded
                    # the error event; don't mask it with a NameError below.
                    return

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
                # Persist session meta (NOT the graph object) so a restart /
                # different worker can recompile and replay the checkpoint.
                save_live_session(session_id, "resume", user_id, {
                    "mode": req.mode.value, "topic": req.topic, "user_id": user_id,
                })
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

    topics = await asyncio.to_thread(load_topics, user_id)
    if not req.topic or req.topic not in topics:
        raise HTTPException(400, f"Invalid topic. Available: {list(topics.keys())}")

    from backend.graphs.drill_pipeline import DrillPipeline

    pipeline = DrillPipeline(topic=req.topic, user_id=user_id, mode=req.mode.value)
    return StreamingResponse(
        pipeline.run(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/interview/chat")
async def chat(req: ChatRequest, user_id: str = Depends(get_current_user)):
    entry = _get_resume_graph(req.session_id, user_id)
    if entry is None:
        raise HTTPException(404, "Session not found. It may have expired.")
    if entry.get("user_id") != user_id:
        raise HTTPException(403, "Access denied.")

    graph = entry["graph"]
    config = entry["config"]

    from backend.utils.sse_helpers import stream_blocking_sse, streaming_response, sse_event

    graph.update_state(config, {"messages": [HumanMessage(content=req.message)]})

    async def _gen():
        result = None
        async for kind, value in stream_blocking_sse(
            graph.invoke, None, config,
            progress_msg="面试官正在思考",
        ):
            if kind == "sse":
                yield value
            else:
                result = value

        append_message(req.session_id, "user", req.message, user_id=user_id)

        # stream_blocking_sse yields no ("result", ...) tuple when the graph step
        # errors (it emits an error event and returns). Bail out instead of
        # dereferencing an unbound `result` and crashing the generator.
        if result is None:
            yield sse_event({"type": "done"})
            return

        is_finished = False
        if isinstance(result, dict):
            is_finished = result.get("is_finished", False)
            phase = result.get("phase", "")
            if phase in (InterviewPhase.END.value, "end"):
                is_finished = True

        ai_message = ""
        for msg in reversed(result.get("messages", [])):
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
    # Re-evaluation is allowed from any state (the 时光机 "重新评估" entry): a
    # prior attempt may have produced an empty / failed review, or none at all.
    # We always overwrite review/scores/overall, but skip the profile/SR/knowledge
    # side-effects when they were already applied so re-evaluating a *completed*
    # session never double-counts.
    #
    # "Already scored" must mean a previous eval actually counted toward those
    # side-effects — NOT merely that score rows exist. A failed eval still
    # persists placeholder rows ({score: None, "评分失败…"}; see decoupled_eval),
    # and skipped questions persist score=0; neither ever applied a side-effect.
    # Gate on ≥1 answered (non-skipped) question with a real numeric score, so
    # re-evaluating a previously-failed session correctly (re)applies profile /
    # SR / knowledge updates instead of silently skipping them.
    existing = await asyncio.to_thread(get_session, session_id, user_id=user_id)
    already_scored = bool(existing) and any(
        isinstance(s.get("score"), (int, float)) and not s.get("skipped")
        for s in (existing.get("scores") or [])
    )

    # -- Drill mode --
    entry = get_live(drill_sessions, session_id, "drill", user_id)
    if entry is None and existing and existing.get("mode") == "topic_drill" and existing.get("questions"):
        # Live entry gone (completed eval cleared it, restart, or TTL eviction):
        # reconstruct from the persisted session so re-evaluation still works.
        entry = {"topic": existing.get("topic"), "questions": existing["questions"], "user_id": user_id}
    if entry:
        if entry.get("user_id") != user_id:
            raise HTTPException(403, "Access denied.")

        topic = entry["topic"]
        questions = entry["questions"]
        answers = _resolve_answers(body, existing, questions)
        save_drill_answers(session_id, answers, user_id=user_id)

        async def _stream_drill():
            eval_result = {}

            # Phase 5A: if a small-tier channel exists, use the decoupled
            # evaluator (parallel per-Q scoring on cheap model + big-model
            # summary). Otherwise fall back to the legacy big-batch stream.
            from backend.graphs.decoupled_eval import has_small_tier, evaluate_decoupled

            if has_small_tier():
                yield sse_event({"type": "eval_progress", "message": "并发评分中…"})
                try:
                    # Stream per-question progress: evaluate_decoupled reports each
                    # completed score via progress_cb → a queue we drain while the
                    # eval task runs, emitting "评分 X/N 题".
                    progress_q: asyncio.Queue = asyncio.Queue()
                    eval_task = asyncio.create_task(
                        evaluate_decoupled(
                            topic, questions, answers, user_id,
                            progress_cb=lambda d, t: progress_q.put_nowait((d, t)),
                        )
                    )
                    while not eval_task.done():
                        try:
                            d, t = await asyncio.wait_for(progress_q.get(), timeout=0.5)
                            yield sse_event({"type": "eval_progress", "message": f"并发评分中… {d}/{t} 题"})
                        except asyncio.TimeoutError:
                            continue
                    while not progress_q.empty():
                        d, t = progress_q.get_nowait()
                        yield sse_event({"type": "eval_progress", "message": f"并发评分中… {d}/{t} 题"})
                    eval_result = await eval_task
                    yield sse_event({"type": "eval_result", "data": eval_result})
                except Exception as exc:
                    yield sse_event({"type": "progress", "message": f"并发评分失败，回退到批量评估: {exc}"})
                    eval_result = {}

            if not eval_result:
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

            # Clamp LLM-emitted RAG scores to 0-10 (or drop) before they feed
            # the metrics, per-question badges, and persisted detail — a model
            # returning 50 must not render as 500%. See clamp_score_0_10.
            from backend.rag_metrics import clamp_score_0_10
            for s in scores:
                if "faithfulness_score" in s:
                    s["faithfulness_score"] = clamp_score_0_10(s.get("faithfulness_score"))
                if "answer_relevance_score" in s:
                    s["answer_relevance_score"] = clamp_score_0_10(s.get("answer_relevance_score"))

            # Emit + persist RAG generation quality metrics (extracted from LLM eval).
            # An answer_eval row is written for EVERY session with ≥1 answered
            # question, so it always shows up in the dashboard — even when the model
            # omitted faithfulness/relevance scores (gen_metrics is None → those
            # columns persist as NULL, instead of the whole row silently vanishing).
            try:
                from backend.rag_metrics import extract_generation_metrics
                from backend.storage.rag_metrics_store import save_rag_metrics
                answered_scores = [s for s in scores if not s.get("skipped")]
                gen_metrics = extract_generation_metrics(scores)
                if gen_metrics:
                    yield sse_event({
                        "type": "rag_eval_metrics",
                        "data": {
                            "faithfulness": round(gen_metrics.faithfulness * 100),
                            "answer_relevance": round(gen_metrics.answer_relevance * 100),
                            "answer_correctness": round(gen_metrics.answer_correctness * 100),
                            "per_question": [
                                {
                                    "question_id": s.get("question_id"),
                                    "faithfulness": s.get("faithfulness_score"),
                                    "answer_relevance": s.get("answer_relevance_score"),
                                }
                                for s in scores if not s.get("skipped")
                            ],
                        },
                    })
                if answered_scores:
                    save_rag_metrics(
                        session_id, user_id, topic, "answer_eval",
                        faithfulness=(gen_metrics.faithfulness if gen_metrics else None),
                        answer_relevance=(gen_metrics.answer_relevance if gen_metrics else None),
                        answer_correctness=(gen_metrics.answer_correctness if gen_metrics else None),
                        chunk_count=len(answered_scores),
                        detail={
                            "per_question": [
                                {"qid": s.get("question_id"),
                                 "f": s.get("faithfulness_score"),
                                 "ar": s.get("answer_relevance_score")}
                                for s in answered_scores
                            ]
                        },
                    )
            except Exception as exc:
                import logging
                logging.getLogger("uvicorn").warning("RAG eval metrics failed: %s", exc)

            # Persist the evaluation outcome. Shielded so a client disconnect during/after
            # the (possibly minutes-long) eval doesn't leave the session evaluated-but-unsaved:
            # answers were saved before streaming; this saves review + SR + profile + knowledge.
            async def _persist_drill():
                try:
                    review_ = format_drill_review(questions, answers, scores, overall)
                    save_review(session_id, review_, scores, overall.get("new_weak_points", []), overall, user_id=user_id)

                    # SR / profile / knowledge are applied once per session. On a
                    # re-evaluation of an already-scored session, skip them so the
                    # stats aren't double-counted (the report itself is refreshed).
                    if not already_scored:
                        from backend.spaced_repetition import update_weak_point_sr
                        for s in scores:
                            wp = s.get("weak_point")
                            sc = s.get("score")
                            if wp and isinstance(sc, (int, float)):
                                update_weak_point_sr(topic, wp, sc, user_id, difficulty=s.get("difficulty", 3))

                        await _update_drill_profile(topic, overall, scores, len(questions), user_id)
                    del_live(drill_sessions, session_id, user_id)

                    if not already_scored:
                        try:
                            from backend.knowledge_evolution import extract_and_writeback, collect_high_freq
                            await extract_and_writeback(topic, questions, answers, scores, user_id)
                            await collect_high_freq(topic, questions, scores, user_id)
                        except Exception as e:
                            import logging
                            logging.getLogger("uvicorn").warning(f"Knowledge evolution failed: {e}")
                        # Stamp the idempotency marker so the "同步" fallback shows
                        # this session as already synced and never re-applies stats.
                        await asyncio.to_thread(mark_session_synced, session_id, user_id=user_id)
                    return review_
                except Exception as e:
                    import logging
                    logging.getLogger("uvicorn").error(f"Drill result persistence failed: {e}")
                    return format_drill_review(questions, answers, scores, overall)

            review = await asyncio.shield(_persist_drill())

            result = {
                "session_id": session_id,
                "mode": "topic_drill",
                "review": review,
                "scores": scores,
                "overall": overall,
            }
            yield sse_event({"type": "complete", "data": result})
            yield sse_event({"type": "done"})

        return streaming_response(_stream_drill())

    # -- JD prep mode --
    entry = get_live(job_prep_sessions, session_id, "job_prep", user_id)
    if entry is None and existing and existing.get("mode") == "jd_prep" and existing.get("questions"):
        meta_ = existing.get("meta", {}) or {}
        entry = {"questions": existing["questions"], "preview": meta_.get("preview", {}), "meta": meta_, "user_id": user_id}
    if entry:
        if entry.get("user_id") != user_id:
            raise HTTPException(403, "Access denied.")

        questions = entry["questions"]
        preview = entry["preview"]
        meta = entry["meta"]
        answers = _resolve_answers(body, existing, questions)
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

            # Shielded persistence — see _persist_drill. Survives a client disconnect.
            async def _persist_job_prep():
                try:
                    review_ = format_job_prep_review(questions, answers, scores, overall, meta)
                    save_review(session_id, review_, scores, overall.get("new_weak_points", []), overall, user_id=user_id)

                    # Skip profile/knowledge side-effects on re-eval (see _persist_drill).
                    if not already_scored:
                        await _update_job_prep_profile(overall, scores, len(questions), meta, user_id)
                    del_live(job_prep_sessions, session_id, user_id)

                    if not already_scored:
                        try:
                            from backend.knowledge_evolution import extract_and_writeback, collect_high_freq
                            jd_topics = _match_jd_to_topics(meta, user_id)
                            for t in jd_topics:
                                await extract_and_writeback(t, questions, answers, scores, user_id)
                                await collect_high_freq(t, questions, scores, user_id)
                        except Exception as e:
                            import logging
                            logging.getLogger("uvicorn").warning(f"JD prep knowledge evolution failed: {e}")
                        # Idempotency marker for the "同步" fallback (see _persist_drill).
                        await asyncio.to_thread(mark_session_synced, session_id, user_id=user_id)
                    return review_
                except Exception as e:
                    import logging
                    logging.getLogger("uvicorn").error(f"JD prep persistence failed: {e}")
                    return format_job_prep_review(questions, answers, scores, overall, meta)

            review = await asyncio.shield(_persist_job_prep())

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
            yield sse_event({"type": "complete", "data": result})
            yield sse_event({"type": "done"})

        return streaming_response(_stream_job_prep())

    # -- Resume mode --
    entry = _get_resume_graph(session_id, user_id)
    if entry is None:
        raise HTTPException(404, "Session not found.")
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
        yield sse_event({"type": "eval_start", "total": len(messages)})

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

        # Shielded persistence — see _persist_drill. The profile extraction (an LLM call)
        # plus review/knowledge writeback complete even if the client disconnects mid-review.
        async def _persist_resume():
            try:
                extraction_ = await update_profile_after_interview(
                    mode=entry["mode"].value,
                    topic=entry.get("topic"),
                    messages=messages,
                    user_id=user_id,
                    scores=scores,
                )

                resume_overall = {}
                if extraction_.get("dimension_scores"):
                    resume_overall["dimension_scores"] = extraction_["dimension_scores"]
                if extraction_.get("avg_score"):
                    resume_overall["avg_score"] = extraction_["avg_score"]
                save_review(session_id, review_text, scores, weak_points, overall=resume_overall, user_id=user_id)

                del_live(graphs, session_id, user_id)

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
                return extraction_
            except Exception as e:
                import logging
                logging.getLogger("uvicorn").error(f"Resume persistence failed: {e}")
                return {}

        extraction = await asyncio.shield(_persist_resume())

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
        yield sse_event({"type": "complete", "data": result})
        yield sse_event({"type": "done"})

    return streaming_response(_stream_resume())


@router.post("/interview/sync/{session_id}")
async def sync_session_side_effects(session_id: str, user_id: str = Depends(get_current_user)):
    """Manual fallback: apply profile / SR / knowledge side-effects from a
    session's ALREADY-persisted scores, without re-running the LLM evaluation.

    For sessions that were scored but whose side-effects never landed — e.g. an
    eval that failed at the per-question level yet still wrote placeholder score
    rows, or a crash between save_review and the profile update. Re-evaluating
    those won't help (they read as already-scored and skip the side-effects), so
    this re-applies them straight from the stored scores/overall/answers.

    Idempotent via meta.synced_at: a second call is a no-op, so EWMA / SR /
    high-freq counters are never double-counted.
    """
    session = await asyncio.to_thread(get_session, session_id, user_id=user_id)
    if not session:
        raise HTTPException(404, "Session not found.")
    mode = session.get("mode")
    if mode not in ("topic_drill", "jd_prep"):
        raise HTTPException(400, "仅训练 / JD 备面会话支持同步到画像。")
    if (session.get("meta") or {}).get("synced_at"):
        return {"status": "already_synced", "synced_at": session["meta"]["synced_at"]}

    scores = session.get("scores") or []
    has_valid = any(
        isinstance(s.get("score"), (int, float)) and not s.get("skipped") for s in scores
    )
    if not has_valid:
        raise HTTPException(400, "该会话没有有效评分，请先重新评估再同步。")

    questions = session.get("questions") or []
    overall = session.get("overall") or {}
    answers = _answers_from_transcript(questions, session.get("transcript", []))

    from backend.knowledge_evolution import extract_and_writeback, collect_high_freq

    if mode == "topic_drill":
        topic = session.get("topic")
        from backend.spaced_repetition import update_weak_point_sr
        for s in scores:
            wp = s.get("weak_point")
            sc = s.get("score")
            if wp and isinstance(sc, (int, float)):
                update_weak_point_sr(topic, wp, sc, user_id, difficulty=s.get("difficulty", 3))
        await _update_drill_profile(topic, overall, scores, len(questions), user_id)
        try:
            await extract_and_writeback(topic, questions, answers, scores, user_id)
            await collect_high_freq(topic, questions, scores, user_id)
        except Exception as e:
            import logging
            logging.getLogger("uvicorn").warning(f"Sync knowledge evolution failed: {e}")
    else:  # jd_prep
        meta = session.get("meta", {}) or {}
        await _update_job_prep_profile(overall, scores, len(questions), meta, user_id)
        try:
            for t in _match_jd_to_topics(meta, user_id):
                await extract_and_writeback(t, questions, answers, scores, user_id)
                await collect_high_freq(t, questions, scores, user_id)
        except Exception as e:
            import logging
            logging.getLogger("uvicorn").warning(f"Sync JD prep knowledge evolution failed: {e}")

    await asyncio.to_thread(mark_session_synced, session_id, user_id=user_id)
    return {"status": "synced"}


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
