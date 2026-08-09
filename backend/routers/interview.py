"""Interview routes — start, chat, end for drill / resume / JD-prep modes."""
import asyncio
import hashlib
import json
import logging
import threading
import weakref

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage

from backend.models import (
    StartInterviewRequest, ChatRequest, EndDrillRequest,
    InterviewMode, InterviewPhase, ReferenceAnswerRequest,
    RetryInterviewReplyRequest,
)
from backend.indexer import load_topics
from backend.memory import update_profile_after_interview, llm_update_profile
from backend.storage.sessions import (
    create_session, append_message, save_review, save_drill_answers, get_session,
    mark_session_synced, try_claim_session_sync, release_session_sync_claim,
    abort_session_sync_claim, session_sync_targets,
    mark_session_sync_step, session_sync_steps, session_sync_step_result,
    try_claim_session_evaluation, release_session_evaluation_claim,
    try_claim_resume_turn, commit_resume_turn, replace_resume_reply,
    release_resume_turn_claim, withdraw_resume_user_tail,
    renew_resume_turn_claim, RESUME_TURN_CLAIM_TTL_SECONDS,
    new_session_id, delete_session, mark_resume_session_initialized,
)
from backend.graphs.resume_interview import compile_resume_interview
from backend.graphs.topic_drill import generate_drill_questions, stream_evaluate_drill_answers
from backend.graphs.job_prep import stream_evaluate_job_prep_answers
from backend.graphs.review import stream_generate_review
from backend.formatters import format_drill_review, format_job_prep_review
from backend.live_store import (
    graphs, drill_sessions, job_prep_sessions,
    save_live, get_live, del_live,
)
from backend.storage.live_sessions import save_live_session, load_live_session
from backend.utils.sse_helpers import chunk_text, sse_event, streaming_response
from backend.auth import get_current_user

router = APIRouter(prefix="/api")
logger = logging.getLogger("uvicorn")

# A resume turn normally finishes well inside this lease, but graph execution
# can include several slow model calls. Keep renewing until the locally
# confirmed lease is close to expiry; transient SQLite contention must not
# permanently disable the heartbeat after one failed renewal.
_RESUME_TURN_CLAIM_TTL_SECONDS = RESUME_TURN_CLAIM_TTL_SECONDS
_RESUME_TURN_HEARTBEAT_SECONDS = 30.0
_RESUME_TURN_HEARTBEAT_RENEWAL_GUARD_SECONDS = 30.0
_RESUME_TURN_HEARTBEAT_MIN_RETRY_SECONDS = 0.01

_resume_session_locks: "weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)
_resume_session_locks_guard = threading.Lock()


def _get_resume_session_lock(user_id: str, session_id: str) -> asyncio.Lock:
    key = (user_id, session_id)
    with _resume_session_locks_guard:
        lock = _resume_session_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _resume_session_locks[key] = lock
        return lock


def _numeric_score(value):
    from backend.rag_metrics import clamp_score_0_10
    return clamp_score_0_10(value)


def _has_valid_side_effect_score(scores: list) -> bool:
    return any(
        isinstance(s, dict) and _numeric_score(s.get("score")) is not None and not s.get("skipped")
        for s in (scores or [])
    )


def _normalize_scores(raw_scores, questions: list | None = None) -> list[dict]:
    """Normalize model output before it reaches persistence or profile updates."""
    question_map = {}
    questions_by_text: dict[str, list[dict]] = {}
    for question in questions or []:
        if isinstance(question, dict) and question.get("id") is not None:
            question_map[question.get("id")] = question
            question_map[str(question.get("id"))] = question
            text = question.get("question")
            if isinstance(text, str) and text.strip():
                questions_by_text.setdefault(text.strip(), []).append(question)
    normalized_by_question: dict[str, dict] = {}
    unmatched = []
    for index, raw in enumerate(raw_scores or []):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        score = _numeric_score(item.get("score"))
        item["score"] = score
        qid = item.get("question_id")
        try:
            question = question_map.get(qid)
        except TypeError:
            # A malformed model response may emit an object/list as the id.
            # Keep the usable score rows instead of failing the whole report.
            question = None
        if question is None:
            question = question_map.get(str(qid))
        if question is None:
            raw_text = item.get("question")
            text = raw_text.strip() if isinstance(raw_text, str) else ""
            text_matches = questions_by_text.get(text, []) if text else []
            if len(text_matches) == 1:
                question = text_matches[0]
            elif not text and qid is None and index < len(questions or []):
                # Legacy model rows sometimes have neither an id nor question
                # text. Positional recovery is safe only in that case; a
                # present-but-ambiguous text must never be guessed by index.
                fallback_question = (questions or [])[index]
                if isinstance(fallback_question, dict):
                    question = fallback_question
        if question:
            item.setdefault("difficulty", question.get("difficulty", 3))
        try:
            raw_difficulty = item.get("difficulty", 3)
            difficulty = 3 if isinstance(raw_difficulty, bool) else int(raw_difficulty)
        except (TypeError, ValueError):
            difficulty = 3
        item["difficulty"] = max(1, min(5, difficulty))
        if question is not None:
            canonical_id = question.get("id")
            item["question_id"] = canonical_id
            normalized_by_question.setdefault(str(canonical_id), item)
        else:
            unmatched.append(item)

    if not questions:
        return [*normalized_by_question.values(), *unmatched]

    normalized = []
    for question in questions:
        if not isinstance(question, dict) or question.get("id") is None:
            continue
        canonical_id = question["id"]
        normalized.append(normalized_by_question.get(str(canonical_id), {
            "question_id": canonical_id,
            "score": None,
            "difficulty": max(1, min(5, int(question.get("difficulty", 3) or 3))),
        }))
    normalized.extend(unmatched)
    return normalized


def _normalize_overall(raw_overall) -> dict:
    overall = dict(raw_overall) if isinstance(raw_overall, dict) else {}
    if "avg_score" in overall:
        overall["avg_score"] = _numeric_score(overall.get("avg_score"))
    return overall


def _normalize_answers(value) -> list[dict]:
    """Normalize legacy dict answers for callers/tests that bypass Pydantic."""
    if value is None:
        return []
    if isinstance(value, dict):
        value = [
            {"question_id": qid, "answer": (a.get("answer", "") if isinstance(a, dict) else a)}
            for qid, a in value.items()
        ]
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        qid = item.get("question_id", item.get("id"))
        if qid is None:
            continue
        answer = item.get("answer", "")
        result.append({**item, "question_id": qid, "answer": "" if answer is None else str(answer)})
    return result


async def _release_eval_claim_after(stream, session_id: str, user_id: str,
                                    claim_token: str):
    """Wrap an SSE generator so disconnects cannot leave an eval claim forever."""
    try:
        async for item in stream:
            yield item
    finally:
        await asyncio.to_thread(
            release_session_evaluation_claim, session_id,
            user_id=user_id, claim_token=claim_token,
        )


async def _finish_despite_cancellation(coro):
    """Wait for a shielded durable write before releasing its generation claim."""
    task = asyncio.create_task(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # asyncio.shield keeps the task alive but normally returns cancellation
        # immediately. Waiting here keeps the evaluation claim held until the
        # durable review/profile/knowledge write actually reaches a terminal state.
        try:
            await task
        finally:
            raise


# Map internal turn-flow errors to messages the user can act on. The generic
# fallback ("请重试") is wrong advice for claim conflicts — retrying while
# another tab holds the turn claim fails identically and reads as flakiness.
_RESUME_TURN_ERROR_MESSAGES = {
    "Resume session is being evaluated or is already complete":
        "该会话正在其他窗口回复或评估中，请稍候或切换到那个窗口继续。",
    "Resume session is being evaluated or updated":
        "该会话正在其他窗口回复或评估中，请稍候或切换到那个窗口继续。",
    "The previous interview reply is pending recovery":
        "上一轮面试官回复尚未完成，请先在页面提示中恢复或修改该回答。",
    "Resume interview is already complete":
        "本场面试已经结束，请点击「查看复盘」。",
}


def _resume_turn_error_message(exc: Exception, fallback: str) -> str:
    return _RESUME_TURN_ERROR_MESSAGES.get(str(exc), fallback)


class _SyncClaimLost(RuntimeError):
    pass


async def _evaluation_generation_is_current(
    session_id: str,
    user_id: str,
    evaluation_token: str,
) -> bool:
    """Check ownership immediately before externally visible completion."""
    latest = await asyncio.to_thread(get_session, session_id, user_id=user_id)
    meta = (latest or {}).get("meta", {}) or {}
    return bool(
        latest
        and isinstance(meta, dict)
        and evaluation_token
        and meta.get("evaluation_claim_token") == evaluation_token
    )


async def _mark_sync_step(session_id: str, step: str, user_id: str,
                          claim_token: str, result: dict | None = None) -> None:
    marked = await asyncio.to_thread(
        mark_session_sync_step, session_id, step,
        user_id=user_id, claim_token=claim_token, result=result,
    )
    if not marked:
        raise _SyncClaimLost(f"sync claim lost while marking {step}")


def _sync_operation_id(session_id: str, step: str) -> str:
    """Stable target-level idempotency key for one session side-effect."""
    return f"session-sync:{session_id}:{step}"


def _sr_operation_id(
    session_id: str,
    score_row: dict,
    questions: list,
    index: int,
) -> str | None:
    """Canonical SR identity independent of score ordering and scalar ID type."""
    question = questions[index] if index < len(questions) else None
    question_id = score_row.get("question_id")
    if question_id is None and isinstance(question, dict):
        question_id = question.get("id")

    if question_id is not None:
        if isinstance(question_id, float) and question_id.is_integer():
            question_id = int(question_id)
        if isinstance(question_id, (str, int)) and not isinstance(question_id, bool):
            identity = f"id:{str(question_id).strip()}"
        else:
            identity = "id:" + json.dumps(
                question_id, ensure_ascii=False, sort_keys=True, default=str,
            )
    else:
        question_text = score_row.get("question")
        if not question_text and isinstance(question, dict):
            question_text = question.get("question")
        if not isinstance(question_text, str) or not question_text.strip():
            return None
        identity = f"question:{question_text.strip()}"

    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return _sync_operation_id(session_id, f"sr:{digest}")


async def _confirm_profile_operations(session_id: str, user_id: str) -> None:
    from backend.memory import confirm_profile_session_operations
    try:
        await asyncio.to_thread(
            confirm_profile_session_operations, user_id, session_id,
        )
    except Exception as exc:
        # The session marker is authoritative. A failed GC confirmation only
        # retains extra recovery records and must not turn success into failure.
        import logging
        logging.getLogger("uvicorn").warning(
            "Could not confirm profile operation journal for %s: %s",
            session_id,
            exc,
        )


async def _skip_deleted_topic_steps(
    session_id: str,
    target_topic: str,
    steps: set[str],
    user_id: str,
    claim_token: str,
) -> bool:
    """Record removed topics as intentionally skipped during recovery."""
    topics = await asyncio.to_thread(load_topics, user_id)
    # An empty registry can also mean a legacy/test installation without a
    # topics.json file; leave that case to the existing writer's error handling.
    # When a non-empty registry no longer contains the frozen key, it is a real
    # deletion and must not recreate a topic during recovery.
    if not isinstance(topics, dict) or not topics or target_topic in topics:
        return False
    for step in (
        f"knowledge_extract:{target_topic}",
        f"high_freq:{target_topic}",
    ):
        if step not in steps:
            await _mark_sync_step(
                session_id,
                step,
                user_id,
                claim_token,
                result={"status": "skipped", "reason": "topic_deleted"},
            )
            steps.add(step)
    return True


async def _apply_drill_side_effects(session_id: str, topic: str, questions: list,
                                    answers: list, scores: list, overall: dict,
                                    user_id: str, claim_token: str) -> bool:
    """Apply drill side-effects with durable per-step idempotency."""
    steps = await asyncio.to_thread(session_sync_steps, session_id, user_id=user_id)
    if "sr" not in steps:
        from backend.spaced_repetition import update_weak_point_sr
        applied_operations: set[str] = set()
        for index, score_row in enumerate(scores):
            weak_point = score_row.get("weak_point")
            score = _numeric_score(score_row.get("score"))
            if weak_point and score is not None:
                operation_id = _sr_operation_id(
                    session_id, score_row, questions, index,
                )
                if not operation_id or operation_id in applied_operations:
                    continue
                applied_operations.add(operation_id)
                update_weak_point_sr(
                    topic, weak_point, score, user_id,
                    difficulty=score_row.get("difficulty", 3),
                    operation_id=operation_id,
                )
        await _mark_sync_step(session_id, "sr", user_id, claim_token)

    if "profile" not in steps:
        await _update_drill_profile(
            topic, overall, scores, len(questions), user_id,
            operation_id=_sync_operation_id(session_id, "profile"),
        )
        await _mark_sync_step(session_id, "profile", user_id, claim_token)

    for target_topic in [topic]:
        extract_step = f"knowledge_extract:{target_topic}"
        freq_step = f"high_freq:{target_topic}"
        if await _skip_deleted_topic_steps(
            session_id, target_topic, steps, user_id, claim_token,
        ):
            continue
        if extract_step not in steps:
            from backend.knowledge_evolution import extract_and_writeback
            result = await extract_and_writeback(
                target_topic, questions, answers, scores, user_id,
                operation_id=_sync_operation_id(session_id, extract_step),
            )
            if result is False:
                raise RuntimeError(f"knowledge extraction failed for {target_topic}")
            await _mark_sync_step(session_id, extract_step, user_id, claim_token)
        if freq_step not in steps:
            from backend.knowledge_evolution import collect_high_freq
            result = await collect_high_freq(
                target_topic, questions, scores, user_id,
                operation_id=_sync_operation_id(session_id, freq_step),
            )
            if result is False:
                raise RuntimeError(f"high-frequency collection failed for {target_topic}")
            await _mark_sync_step(session_id, freq_step, user_id, claim_token)

    synced = await asyncio.to_thread(
        mark_session_synced, session_id,
        user_id=user_id, claim_token=claim_token,
    )
    if synced:
        await _confirm_profile_operations(session_id, user_id)
    return synced


async def _apply_job_prep_side_effects(session_id: str, questions: list,
                                       answers: list, scores: list, overall: dict,
                                       meta: dict, user_id: str,
                                       claim_token: str,
                                       target_topics: list[str] | None = None) -> bool:
    steps = await asyncio.to_thread(session_sync_steps, session_id, user_id=user_id)
    if "profile" not in steps:
        await _update_job_prep_profile(
            overall, scores, len(questions), meta, user_id,
            operation_id=_sync_operation_id(session_id, "profile"),
        )
        await _mark_sync_step(session_id, "profile", user_id, claim_token)

    from backend.knowledge_evolution import extract_and_writeback, collect_high_freq
    for target_topic in (
        target_topics if target_topics is not None
        else _match_jd_to_topics(meta, user_id)
    ):
        extract_step = f"knowledge_extract:{target_topic}"
        freq_step = f"high_freq:{target_topic}"
        if await _skip_deleted_topic_steps(
            session_id, target_topic, steps, user_id, claim_token,
        ):
            continue
        if extract_step not in steps:
            result = await extract_and_writeback(
                target_topic, questions, answers, scores, user_id,
                operation_id=_sync_operation_id(session_id, extract_step),
            )
            if result is False:
                raise RuntimeError(f"knowledge extraction failed for {target_topic}")
            await _mark_sync_step(session_id, extract_step, user_id, claim_token)
        if freq_step not in steps:
            result = await collect_high_freq(
                target_topic, questions, scores, user_id,
                operation_id=_sync_operation_id(session_id, freq_step),
            )
            if result is False:
                raise RuntimeError(f"high-frequency collection failed for {target_topic}")
            await _mark_sync_step(session_id, freq_step, user_id, claim_token)

    synced = await asyncio.to_thread(
        mark_session_synced, session_id,
        user_id=user_id, claim_token=claim_token,
    )
    if synced:
        await _confirm_profile_operations(session_id, user_id)
    return synced

def _answers_from_transcript(questions: list, transcript: list) -> list:
    """Rebuild ``[{question_id, answer}]`` from a persisted drill transcript.

    ``save_drill_answers`` writes the transcript as question/answer pairs in
    question order (assistant=question, then an optional user=answer). This
    recovers the originally-submitted answers when re-evaluating a session whose
    live entry is gone (completed eval, restart, TTL eviction).
    """
    id_to_answer: dict[str, str] = {}
    text_to_answer: dict[str, str] = {}
    msgs = transcript or []
    i = 0
    while i < len(msgs):
        m = msgs[i]
        if m.get("role") == "assistant":
            ans = ""
            if i + 1 < len(msgs) and msgs[i + 1].get("role") == "user":
                raw = msgs[i + 1].get("content", "")
                ans = "" if raw is None else str(raw)
                i += 1
            question_id = m.get("question_id")
            if question_id is not None:
                id_to_answer[str(question_id)] = ans
            else:
                # Compatibility with transcripts written before question IDs
                # were persisted. New transcripts never rely on question text,
                # which may be duplicated within a session.
                text_to_answer[m.get("content", "")] = ans
        i += 1
    return [
        {
            "question_id": q["id"],
            "answer": id_to_answer.get(
                str(q["id"]), text_to_answer.get(q.get("question", ""), ""),
            ),
        }
        for q in questions
    ]


def _canonicalize_answers(questions: list, answers: list) -> list[dict]:
    """Return exactly one answer per question, in immutable question order."""
    answer_map: dict[str, str] = {}
    for answer in answers or []:
        if not isinstance(answer, dict):
            continue
        question_id = answer.get("question_id", answer.get("id"))
        if question_id is None:
            continue
        value = answer.get("answer", "")
        answer_map[str(question_id)] = "" if value is None else str(value)
    return [
        {
            "question_id": question["id"],
            "answer": answer_map.get(str(question["id"]), ""),
        }
        for question in questions
        if isinstance(question, dict) and question.get("id") is not None
    ]


def _resolve_answers(body, existing: dict | None, questions: list) -> list:
    """Pick the richer answer source: the request body vs the persisted transcript.

    On a first submit the body carries the answers. On re-evaluation the live
    answers may be gone or the restored progress stale, so the transcript is the
    authoritative record. Whichever has more non-empty answers wins.
    """
    body_answers = _canonicalize_answers(
        questions, _normalize_answers(body.answers if body else None),
    )
    body_filled = sum(1 for a in body_answers if str(a.get("answer", "")).strip())
    transcript_answers = _answers_from_transcript(questions, (existing or {}).get("transcript", []))
    transcript_filled = sum(1 for a in transcript_answers if str(a.get("answer", "")).strip())
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
    cached = graphs.get(session_id)
    if cached is not None:
        return cached
    # Prefer the live_sessions meta (type-scoped so a drill/JD row can never be
    # mistaken for a resume one). Fall back to the durable `sessions` row when the
    # live row has been TTL-cleaned (>24h) or was written by a different worker:
    # the graph STATE lives in the checkpoint DB (never TTL'd), so a recompiled
    # graph replays it. Recovery must not hinge on the 24h-expiring live row.
    meta = load_live_session(session_id, user_id, "resume")
    if meta and meta.get("initialization_status") == "pending":
        return None
    if not meta:
        row = get_session(session_id, user_id=user_id)
        if not row or row.get("mode") != InterviewMode.RESUME.value:
            return None
        if (row.get("meta") or {}).get("initialization_status") == "pending":
            return None
        meta = {
            "mode": row.get("mode"),
            "topic": row.get("topic"),
            "user_id": user_id,
        }
    entry = {
        "graph": compile_resume_interview(meta["user_id"]),
        "config": {"configurable": {"thread_id": session_id}},
        "mode": InterviewMode(meta.get("mode", InterviewMode.RESUME.value)),
        "topic": meta.get("topic"),
        "user_id": meta["user_id"],
    }
    graphs[session_id] = entry
    return entry


def _initialize_resume_interview(
    graph,
    initial_state: dict,
    config: dict,
    session_id: str,
    mode: str,
    topic: str | None,
    user_id: str,
) -> str:
    session_created = False
    try:
        create_session(
            session_id,
            mode,
            topic,
            meta={"initialization_status": "pending"},
            user_id=user_id,
        )
        session_created = True
        save_live_session(session_id, "resume", user_id, {
            "mode": mode,
            "topic": topic,
            "user_id": user_id,
            "initialization_status": "pending",
        })
        result = graph.invoke(initial_state, config)
        ai_message = ""
        for msg in reversed(result["messages"]):
            if isinstance(msg, AIMessage):
                ai_message = msg.content
                break
        if not append_message(session_id, "assistant", ai_message, user_id=user_id):
            raise RuntimeError("Resume session disappeared during initialization")
        if not mark_resume_session_initialized(session_id, user_id=user_id):
            raise RuntimeError("Resume session initialization state was lost")
        entry = {
            "graph": graph,
            "config": config,
            "mode": InterviewMode(mode),
            "topic": topic,
            "user_id": user_id,
        }
        graphs[session_id] = entry
        save_live_session(session_id, "resume", user_id, {
            "mode": mode,
            "topic": topic,
            "user_id": user_id,
            "initialization_status": "ready",
        })
        return ai_message
    except Exception:
        graphs.pop(session_id, None)
        if session_created:
            delete_session(session_id, user_id=user_id)
        try:
            from backend.graphs.checkpointer import delete_thread_checkpoints
            delete_thread_checkpoints(session_id)
        except Exception as cleanup_error:
            logger.error(
                "Could not clean failed resume checkpoint %s: %s",
                session_id,
                cleanup_error,
            )
        raise


def _invoke_resume_turn(graph, config: dict, message: str):
    """Run one checkpointed graph turn and preserve whether input was accepted."""
    graph.update_state(config, {"messages": [HumanMessage(content=message)]})
    try:
        return graph.invoke(None, config), None
    except Exception as exc:
        return None, exc


def _resume_graph_values(entry: dict) -> dict:
    """Best-effort state snapshot used when replaying a durable reply."""
    try:
        snapshot = entry["graph"].get_state(entry["config"])
        values = getattr(snapshot, "values", None)
        return dict(values) if isinstance(values, dict) else {}
    except Exception as exc:
        logger.warning("Could not read resume graph state for replay: %s", exc)
        return {}


def _completed_resume_reply(
    transcript: list[dict], message: str,
) -> tuple[bool, str]:
    """Return the durable reply only when it belongs to ``message``."""
    if (
        len(transcript) >= 2
        and transcript[-1].get("role") == "assistant"
        and transcript[-2].get("role") == "user"
        and str(transcript[-2].get("content") or "").strip() == message
    ):
        return True, str(transcript[-1].get("content") or "")
    return False, ""


def _current_resume_reply(result: dict, message: str) -> str:
    """Return the non-empty assistant reply produced after this human input."""
    messages = result.get("messages", []) if isinstance(result, dict) else []
    human_index = None
    for index in range(len(messages) - 1, -1, -1):
        candidate = messages[index]
        if (
            isinstance(candidate, HumanMessage)
            and str(candidate.content or "").strip() == message
        ):
            human_index = index
            break
    if human_index is None:
        raise RuntimeError("Resume graph result did not contain the current input")

    for candidate in messages[human_index + 1:]:
        if isinstance(candidate, AIMessage):
            reply = chunk_text(candidate).strip()
            if reply:
                return reply
    raise RuntimeError("Resume interviewer returned no new assistant reply")


async def _resume_turn_heartbeat(
    session_id: str,
    user_id: str,
    claim_token: str,
    stop: asyncio.Event,
) -> None:
    """Keep a long-running graph turn fenced against evaluation takeover.

    A failed database call is not itself proof that another worker reclaimed the
    turn. Retry while the last confirmed lease still has time left, but stop as
    soon as the storage operation explicitly reports that the token is no
    longer current. ``CancelledError`` is intentionally allowed to propagate.
    """
    loop = asyncio.get_running_loop()
    confirmed_until = loop.time() + _RESUME_TURN_CLAIM_TTL_SECONDS
    while True:
        remaining = confirmed_until - loop.time()
        if remaining <= _RESUME_TURN_HEARTBEAT_RENEWAL_GUARD_SECONDS:
            logger.warning(
                "Could not confirm resume turn lease before expiry for %s",
                session_id,
            )
            return
        retry_in = min(
            max(
                _RESUME_TURN_HEARTBEAT_MIN_RETRY_SECONDS,
                _RESUME_TURN_HEARTBEAT_SECONDS,
            ),
            max(
                _RESUME_TURN_HEARTBEAT_MIN_RETRY_SECONDS,
                remaining - _RESUME_TURN_HEARTBEAT_RENEWAL_GUARD_SECONDS,
            ),
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=retry_in)
            return
        except asyncio.TimeoutError:
            pass
        try:
            renewed = await asyncio.to_thread(
                renew_resume_turn_claim,
                session_id,
                user_id=user_id,
                claim_token=claim_token,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # SQLite can be briefly busy while another claim transaction commits.
            # Keep the old lease authoritative and retry until its bounded guard
            # window; a single transient error must not abandon a long turn.
            logger.warning(
                "Could not renew resume turn lease for %s: %s", session_id, exc,
            )
            if loop.time() >= confirmed_until - _RESUME_TURN_HEARTBEAT_RENEWAL_GUARD_SECONDS:
                logger.warning(
                    "Could not confirm resume turn lease before expiry for %s",
                    session_id,
                )
                return
            continue
        if not renewed:
            # False is the storage-level fencing signal (token mismatch,
            # completed session, or an already reclaimed lease), unlike an
            # exception which may be transient infrastructure failure.
            return
        confirmed_until = loop.time() + _RESUME_TURN_CLAIM_TTL_SECONDS


async def _commit_resume_turn(entry: dict, session_id: str, message: str,
                              user_id: str, *, retry_pending: bool = False) -> tuple[dict, str]:
    """Serialize a resume turn through graph completion and transcript commit.

    ``retry_pending`` is used after a client lost the SSE connection while the
    model failed before producing an assistant message. The human message is
    already in the checkpoint/transcript in that case, so updating the graph
    with it again would duplicate the turn.
    """
    async with _get_resume_session_lock(user_id, session_id):
        retry_request = retry_pending
        if not retry_request:
            stored = await asyncio.to_thread(
                get_session, session_id, user_id=user_id,
            )
            transcript = (stored or {}).get("transcript") or []
            if transcript and transcript[-1].get("role") == "user":
                raise RuntimeError(
                    "The previous interview reply is pending recovery"
                )
        if retry_request:
            stored = await asyncio.to_thread(
                get_session, session_id, user_id=user_id,
            )
            transcript = (stored or {}).get("transcript") or []
            if not stored or stored.get("mode") != InterviewMode.RESUME.value:
                raise RuntimeError("Resume session no longer exists")
            if not transcript:
                raise RuntimeError("No interview reply is available to retry")
            completed, reply = _completed_resume_reply(transcript, message)
            if completed:
                recovered = await asyncio.to_thread(_resume_graph_values, entry)
                recovered["_recovered"] = True
                return recovered, reply
            if transcript[-1].get("role") == "user":
                if str(transcript[-1].get("content") or "").strip() != message:
                    raise RuntimeError("A different interview turn is pending")
                retry_pending = True
            elif transcript[-1].get("role") == "assistant":
                # The failed request never reached the backend. Submit the
                # preserved client message as a new graph turn.
                retry_pending = False
            else:
                raise RuntimeError("The last interview turn cannot be retried")

        if not retry_pending:
            current = await asyncio.to_thread(_resume_graph_values, entry)
            if current.get("is_finished") or current.get("phase") in (
                InterviewPhase.END.value,
                "end",
            ):
                raise RuntimeError("Resume interview is already complete")

        turn_token = await asyncio.to_thread(
            try_claim_resume_turn, session_id, user_id=user_id,
        )
        if not turn_token:
            raise RuntimeError("Resume session is being evaluated or is already complete")

        committed = False
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            _resume_turn_heartbeat(
                session_id, user_id, turn_token, heartbeat_stop,
            )
        )
        try:
            if retry_request:
                # Re-check after the durable claim. In a multi-worker deployment
                # the original request may have committed while this worker was
                # waiting to claim; replay that result instead of advancing the
                # graph a second time without a new human answer.
                latest = await asyncio.to_thread(
                    get_session, session_id, user_id=user_id,
                )
                transcript = (latest or {}).get("transcript") or []
                completed, reply = _completed_resume_reply(transcript, message)
                if completed:
                    recovered = await asyncio.to_thread(_resume_graph_values, entry)
                    recovered["_recovered"] = True
                    return recovered, reply
                if transcript and transcript[-1].get("role") == "user":
                    if str(transcript[-1].get("content") or "").strip() != message:
                        raise RuntimeError("A different interview turn is pending")
                    retry_pending = True
                elif transcript and transcript[-1].get("role") == "assistant":
                    retry_pending = False
                else:
                    raise RuntimeError("The last interview turn cannot be retried")

            if retry_pending:
                try:
                    result = await asyncio.to_thread(
                        entry["graph"].invoke, None, entry["config"],
                    )
                    invoke_error = None
                except Exception as exc:
                    result, invoke_error = None, exc
            else:
                result, invoke_error = await asyncio.to_thread(
                    _invoke_resume_turn, entry["graph"], entry["config"], message,
                )
            ai_message = ""
            if invoke_error is None:
                try:
                    ai_message = _current_resume_reply(result, message)
                except Exception as exc:
                    invoke_error = exc

            if invoke_error is not None:
                turn_messages = [] if retry_pending else [{"role": "user", "content": message}]
            else:
                turn_messages = [{"role": "assistant", "content": ai_message}]
                if not retry_pending:
                    turn_messages.insert(0, {"role": "user", "content": message})

            if not turn_messages:
                raise invoke_error

            committed_phase = None
            committed_finished = None
            if invoke_error is None and isinstance(result, dict):
                raw_phase = result.get("phase")
                if raw_phase is not None:
                    committed_phase = (
                        raw_phase.value
                        if isinstance(raw_phase, InterviewPhase)
                        else str(raw_phase)
                    )
                if "is_finished" in result:
                    committed_finished = bool(result.get("is_finished"))
                if committed_phase in (InterviewPhase.END.value, "end"):
                    committed_finished = True

            persisted = await asyncio.to_thread(
                commit_resume_turn,
                session_id,
                turn_messages,
                user_id=user_id,
                claim_token=turn_token,
                phase=committed_phase,
                is_finished=committed_finished,
            )
            if not persisted:
                raise RuntimeError("Resume transcript claim was lost") from invoke_error
            committed = True
            if invoke_error is not None:
                raise invoke_error
            return result, ai_message
        finally:
            heartbeat_stop.set()
            try:
                await heartbeat_task
            except Exception:
                pass
            if not committed:
                await asyncio.to_thread(
                    release_resume_turn_claim,
                    session_id,
                    user_id=user_id,
                    claim_token=turn_token,
                )


async def _run_resume_turn(entry: dict, session_id: str, message: str,
                           user_id: str) -> tuple[dict, str]:
    """Finish a checkpointed turn even when its awaiting request is cancelled."""
    return await _finish_despite_cancellation(
        _commit_resume_turn(entry, session_id, message, user_id),
    )


async def _run_resume_retry(entry: dict, session_id: str, message: str,
                            user_id: str) -> tuple[dict, str]:
    """Recover a completed reply or resume the last failed graph turn."""
    return await _finish_despite_cancellation(
        _commit_resume_turn(
            entry, session_id, message, user_id, retry_pending=True,
        ),
    )


def _regenerate_resume_graph_reply(entry: dict, message: str) -> tuple[dict, str]:
    """Replace the latest graph reply from the state immediately before it ran."""
    graph = entry["graph"]
    config = entry["config"]
    latest = graph.get_state(config)
    latest_values = getattr(latest, "values", None)
    latest_messages = (
        list(latest_values.get("messages", []))
        if isinstance(latest_values, dict) else []
    )
    if not latest_messages or not isinstance(latest_messages[-1], AIMessage):
        raise RuntimeError("The latest graph reply is not replaceable")
    replaced_message_id = getattr(latest_messages[-1], "id", None)
    if not replaced_message_id:
        raise RuntimeError("The latest graph reply has no stable message id")

    pre_ask = None
    for snapshot in graph.get_state_history(config, limit=100):
        values = getattr(snapshot, "values", None)
        state_messages = (
            list(values.get("messages", []))
            if isinstance(values, dict) else []
        )
        next_nodes = tuple(getattr(snapshot, "next", ()) or ())
        if (
            "ask" in next_nodes
            and state_messages
            and isinstance(state_messages[-1], HumanMessage)
            and str(state_messages[-1].content or "").strip() == message
        ):
            pre_ask = dict(values)
            break
    if pre_ask is None:
        raise RuntimeError("The previous interviewer state is no longer available")

    generated = graph.nodes["ask"].invoke(pre_ask, config)
    generated_messages = generated.get("messages", [])
    if not generated_messages or not isinstance(generated_messages[-1], AIMessage):
        raise RuntimeError("Resume interviewer returned no replacement reply")
    replacement_text = chunk_text(generated_messages[-1]).strip()
    if not replacement_text:
        raise RuntimeError("Resume interviewer returned an empty replacement reply")

    replacement = AIMessage(content=replacement_text, id=replaced_message_id)
    updates = {"messages": [replacement]}
    for key, default in (
        ("phase", "greeting"),
        ("questions_asked", []),
        ("phase_question_count", 0),
        ("is_finished", False),
        ("last_eval", {}),
        ("eval_history", []),
    ):
        updates[key] = generated.get(key, pre_ask.get(key, default))
    graph.update_state(config, updates, as_node="ask")
    updated = graph.get_state(config)
    updated_values = getattr(updated, "values", None)
    if not isinstance(updated_values, dict):
        raise RuntimeError("The regenerated graph state could not be read")
    return dict(updated_values), replacement_text


async def _commit_resume_regeneration(
    entry: dict, session_id: str, message: str, user_id: str,
) -> tuple[dict, str]:
    """Regenerate and atomically replace one completed resume-interview reply."""
    async with _get_resume_session_lock(user_id, session_id):
        stored = await asyncio.to_thread(get_session, session_id, user_id=user_id)
        transcript = (stored or {}).get("transcript") or []
        completed, _old_reply = _completed_resume_reply(transcript, message)
        if not stored or stored.get("mode") != InterviewMode.RESUME.value:
            raise RuntimeError("Resume session no longer exists")
        if not completed:
            raise RuntimeError("No completed interview reply is available to regenerate")
        expected_user_message = str(transcript[-2].get("content") or "")

        turn_token = await asyncio.to_thread(
            try_claim_resume_turn, session_id, user_id=user_id,
        )
        if not turn_token:
            raise RuntimeError("Resume session is being evaluated or updated")

        committed = False
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            _resume_turn_heartbeat(
                session_id, user_id, turn_token, heartbeat_stop,
            )
        )
        try:
            result, ai_message = await asyncio.to_thread(
                _regenerate_resume_graph_reply, entry, message,
            )
            raw_phase = result.get("phase")
            committed_phase = (
                raw_phase.value
                if isinstance(raw_phase, InterviewPhase)
                else str(raw_phase)
            ) if raw_phase is not None else None
            committed_finished = bool(result.get("is_finished", False))
            if committed_phase in (InterviewPhase.END.value, "end"):
                committed_finished = True
            persisted = await asyncio.to_thread(
                replace_resume_reply,
                session_id,
                user_id=user_id,
                claim_token=turn_token,
                expected_user_message=expected_user_message,
                assistant_message=ai_message,
                phase=committed_phase,
                is_finished=committed_finished,
            )
            if not persisted:
                raise RuntimeError("Resume transcript claim was lost")
            committed = True
            return result, ai_message
        finally:
            heartbeat_stop.set()
            try:
                await heartbeat_task
            except Exception:
                pass
            if not committed:
                await asyncio.to_thread(
                    release_resume_turn_claim,
                    session_id,
                    user_id=user_id,
                    claim_token=turn_token,
                )


async def _run_resume_regeneration(
    entry: dict, session_id: str, message: str, user_id: str,
) -> tuple[dict, str]:
    return await _finish_despite_cancellation(
        _commit_resume_regeneration(entry, session_id, message, user_id),
    )


@router.get("/interview/rag-metrics")
async def get_rag_metrics(
    topic: str | None = Query(default=None, max_length=200),
    stage: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
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
    session_id = new_session_id()

    if req.mode == InterviewMode.TOPIC_DRILL:
        topics = await asyncio.to_thread(load_topics, user_id)
        if not req.topic or req.topic not in topics:
            raise HTTPException(400, f"Invalid topic. Available: {list(topics.keys())}")
        try:
            questions = await asyncio.to_thread(generate_drill_questions, req.topic, user_id)
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        await asyncio.to_thread(
            create_session, session_id, req.mode.value, req.topic,
            questions=questions, user_id=user_id,
        )
        await asyncio.to_thread(
            save_live, drill_sessions, session_id, "drill", user_id,
            {"topic": req.topic, "questions": questions, "user_id": user_id},
        )
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
                ai_message = None
                async for kind, value in stream_blocking_sse(
                    _initialize_resume_interview,
                    graph,
                    initial_state,
                    config,
                    session_id,
                    req.mode.value,
                    req.topic,
                    user_id,
                    progress_msg="正在准备面试",
                ):
                    if kind == "sse":
                        yield value
                    else:
                        ai_message = value

                if ai_message is None:
                    return
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

    from backend.utils.sse_helpers import streaming_response, sse_event

    async def _gen():
        turn_task = asyncio.create_task(
            _run_resume_turn(entry, req.session_id, req.message, user_id),
        )
        try:
            yield sse_event({"type": "progress", "message": "面试官正在思考..."})
            while not turn_task.done():
                done, _ = await asyncio.wait({turn_task}, timeout=5.0)
                if not done:
                    yield sse_event({"type": "ping"})

            result, ai_message = turn_task.result()
        except (asyncio.CancelledError, GeneratorExit):
            # Closing an SSE response does not stop a running sync graph call.
            # Keep the lock and wait for the checkpoint + transcript commit so
            # the next request cannot observe or extend half of this turn.
            try:
                await asyncio.shield(turn_task)
            except asyncio.CancelledError:
                try:
                    await turn_task
                except Exception as exc:
                    import logging
                    logging.getLogger("uvicorn").error(
                        "Resume turn failed after client disconnect: %s", exc,
                    )
            except Exception as exc:
                import logging
                logging.getLogger("uvicorn").error(
                    "Resume turn failed after client disconnect: %s", exc,
                )
            raise
        except Exception as exc:
            import logging
            logging.getLogger("uvicorn").error("Resume chat failed: %s", exc)
            yield sse_event({
                "type": "error",
                "message": _resume_turn_error_message(
                    exc, "面试回复生成失败，请重试。",
                ),
            })
            yield sse_event({"type": "done"})
            return

        is_finished = False
        if isinstance(result, dict):
            is_finished = result.get("is_finished", False)
            phase = result.get("phase", "")
            if phase in (InterviewPhase.END.value, "end"):
                is_finished = True

        yield sse_event({"type": "complete", "data": {
            "session_id": req.session_id, "message": ai_message, "is_finished": is_finished,
        }})
        yield sse_event({"type": "done"})

    return streaming_response(_gen())


@router.post("/interview/regenerate/{session_id}")
async def regenerate_resume_reply(
    session_id: str,
    body: RetryInterviewReplyRequest,
    user_id: str = Depends(get_current_user),
):
    """Recover or replace the last resume-interview reply without duplicating input.

    If the original request finished after the browser lost its SSE connection,
    the durable assistant message is replayed. If generation failed before an
    assistant message was committed, LangGraph resumes from its pending
    checkpoint and only the missing assistant message is appended. With
    ``force=true``, a completed reply is regenerated from its pre-ask graph
    state and replaces the durable assistant message.
    """
    entry = _get_resume_graph(session_id, user_id)
    if entry is None:
        raise HTTPException(404, "Session not found. It may have expired.")
    if entry.get("user_id") != user_id:
        raise HTTPException(403, "Access denied.")

    async def _gen():
        runner = _run_resume_regeneration if body.force else _run_resume_retry
        turn_task = asyncio.create_task(
            runner(entry, session_id, body.message, user_id),
        )
        try:
            progress_message = (
                "正在重新生成上一轮面试回复..."
                if body.force
                else "正在恢复上一轮面试回复..."
            )
            yield sse_event({"type": "progress", "message": progress_message})
            while not turn_task.done():
                done, _ = await asyncio.wait({turn_task}, timeout=5.0)
                if not done:
                    yield sse_event({"type": "ping"})
            result, ai_message = turn_task.result()
        except (asyncio.CancelledError, GeneratorExit):
            try:
                await asyncio.shield(turn_task)
            except asyncio.CancelledError:
                try:
                    await turn_task
                except Exception as exc:
                    logger.error("Resume retry failed after client disconnect: %s", exc)
            except Exception as exc:
                logger.error("Resume retry failed after client disconnect: %s", exc)
            raise
        except Exception as exc:
            logger.error("Resume reply retry failed: %s", exc)
            error_message = _resume_turn_error_message(
                exc,
                "上一轮回复重新生成失败，请稍后重试。"
                if body.force
                else "上一轮回复暂时无法恢复，请稍后重试。",
            )
            yield sse_event({
                "type": "error",
                "message": error_message,
            })
            yield sse_event({"type": "done"})
            return

        is_finished = False
        if isinstance(result, dict):
            is_finished = bool(result.get("is_finished", False))
            if result.get("phase") in (InterviewPhase.END.value, "end"):
                is_finished = True

        yield sse_event({"type": "complete", "data": {
            "session_id": session_id,
            "message": ai_message,
            "is_finished": is_finished,
            "recovered": bool(isinstance(result, dict) and result.get("_recovered")),
            "regenerated": body.force,
        }})
        yield sse_event({"type": "done"})

    return streaming_response(_gen())


@router.post("/interview/withdraw/{session_id}")
async def withdraw_resume_turn(
    session_id: str,
    body: RetryInterviewReplyRequest,
    user_id: str = Depends(get_current_user),
):
    """Abandon a pending (unanswered) resume turn so the answer can be edited.

    Only applies when the transcript tail is exactly the given user message with
    no assistant reply after it — i.e. generation failed or never completed. The
    message is removed from both the durable transcript and the LangGraph
    checkpoint under a turn claim, so a concurrently completing worker either
    wins (its commit lands first and this withdraw is rejected) or loses (its
    fenced token can no longer commit).
    """
    entry = _get_resume_graph(session_id, user_id)
    if entry is None:
        raise HTTPException(404, "Session not found. It may have expired.")
    if entry.get("user_id") != user_id:
        raise HTTPException(403, "Access denied.")

    message = (body.message or "").strip()
    if not message:
        raise HTTPException(400, "A message is required.")

    async with _get_resume_session_lock(user_id, session_id):
        stored = await asyncio.to_thread(get_session, session_id, user_id=user_id)
        if not stored or stored.get("mode") != InterviewMode.RESUME.value:
            raise HTTPException(404, "Session not found.")
        transcript = stored.get("transcript") or []
        last = transcript[-1] if transcript else None
        if not last or last.get("role") != "user":
            # Nothing durable to withdraw (the failed send never reached the
            # backend). Report success so the client can clear its local state.
            return {"ok": True, "withdrawn": False}
        if str(last.get("content") or "").strip() != message:
            raise HTTPException(409, "A different interview turn is pending.")

        turn_token = await asyncio.to_thread(
            try_claim_resume_turn, session_id, user_id=user_id,
        )
        if not turn_token:
            raise HTTPException(
                409, "该会话正在其他窗口回复或评估中，请稍候再试。",
            )
        released = False
        try:
            withdrawn = await asyncio.to_thread(
                withdraw_resume_user_tail,
                session_id,
                user_id=user_id,
                claim_token=turn_token,
                expected_message=str(last.get("content") or ""),
            )
            released = withdrawn  # the UPDATE releases the claim on success
            if not withdrawn:
                # Tail changed between our read and the claim (another worker
                # committed the assistant reply). Surface as a conflict.
                raise HTTPException(
                    409, "该回合刚刚已完成回复，请刷新查看最新对话。",
                )

            # Mirror the removal into the graph checkpoint so the next invoke
            # doesn't resume the abandoned turn. Best-effort: transcript is the
            # durable source the chat path re-checks first.
            def _drop_checkpoint_tail():
                from langchain_core.messages import RemoveMessage
                graph = entry["graph"]
                config = entry["config"]
                snapshot = graph.get_state(config)
                values = getattr(snapshot, "values", None) or {}
                messages = list(values.get("messages", []))
                if (
                    messages
                    and isinstance(messages[-1], HumanMessage)
                    and str(messages[-1].content or "").strip() == message
                    and getattr(messages[-1], "id", None)
                ):
                    graph.update_state(
                        config,
                        {"messages": [RemoveMessage(id=messages[-1].id)]},
                        as_node="wait",
                    )
            try:
                await asyncio.to_thread(_drop_checkpoint_tail)
            except Exception as exc:
                logger.warning(
                    "Could not drop withdrawn turn from checkpoint %s: %s",
                    session_id, exc,
                )
            return {"ok": True, "withdrawn": True}
        finally:
            if not released:
                await asyncio.to_thread(
                    release_resume_turn_claim,
                    session_id, user_id=user_id, claim_token=turn_token,
                )


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
    already_synced = bool(existing) and bool(
        (existing.get("meta") or {}).get("synced_at")
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
        evaluation_token = await asyncio.to_thread(
            try_claim_session_evaluation, session_id, user_id=user_id,
        )
        if not evaluation_token:
            latest = await asyncio.to_thread(
                get_session, session_id, user_id=user_id,
            )
            latest_meta = (latest or {}).get("meta", {}) or {}
            if not latest_meta.get("synced_at") and (
                latest_meta.get("sync_pending_at")
                or latest_meta.get("sync_steps")
            ):
                raise HTTPException(
                    409,
                    "Previous evaluation side-effects are pending; sync them "
                    "before re-evaluating this session.",
                )
            raise HTTPException(409, "Evaluation is already in progress for this session.")
        try:
            if not save_drill_answers(
                session_id, answers, user_id=user_id,
                evaluation_token=evaluation_token,
            ):
                raise _SyncClaimLost(
                    "evaluation generation changed before answers were saved"
                )
        except Exception:
            await asyncio.to_thread(
                release_session_evaluation_claim, session_id,
                user_id=user_id, claim_token=evaluation_token,
            )
            raise

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

            scores = _normalize_scores(eval_result.get("scores", []), questions)
            overall = _normalize_overall(eval_result.get("overall", {}))

            # Clamp LLM-emitted RAG scores to 0-10 (or drop) before they feed
            # the metrics, per-question badges, and persisted detail — a model
            # returning 50 must not render as 500%. See clamp_score_0_10.
            from backend.rag_metrics import clamp_score_0_10
            for s in scores:
                if "faithfulness_score" in s:
                    s["faithfulness_score"] = clamp_score_0_10(s.get("faithfulness_score"))
                if "answer_relevance_score" in s:
                    s["answer_relevance_score"] = clamp_score_0_10(s.get("answer_relevance_score"))

            # Extract RAG generation quality metrics from the evaluation. The
            # durable row is written only after save_review succeeds below, so
            # an expired evaluation claim cannot leave a late metrics record.
            # An answer_eval row is written for EVERY session with ≥1 answered
            # question, so it always shows up in the dashboard — even when the model
            # omitted faithfulness/relevance scores (gen_metrics is None → those
            # columns persist as NULL, instead of the whole row silently vanishing).
            answered_scores = []
            gen_metrics = None
            metric_detail = {}
            metric_writer = None
            try:
                from backend.rag_metrics import extract_generation_metrics
                from backend.storage.rag_metrics_store import save_rag_metrics
                metric_writer = save_rag_metrics
                answered_scores = [s for s in scores if not s.get("skipped")]
                gen_metrics = extract_generation_metrics(scores)
                metric_detail = {
                    "per_question": [
                        {"qid": s.get("question_id"),
                         "f": s.get("faithfulness_score"),
                         "ar": s.get("answer_relevance_score")}
                        for s in answered_scores
                    ]
                }
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
            except Exception as exc:
                import logging
                logging.getLogger("uvicorn").warning("RAG eval metrics failed: %s", exc)

            # Persist the evaluation outcome. Shielded so a client disconnect during/after
            # the (possibly minutes-long) eval doesn't leave the session evaluated-but-unsaved:
            # answers were saved before streaming; this saves review + SR + profile + knowledge.
            async def _persist_drill():
                try:
                    review_ = format_drill_review(questions, answers, scores, overall)
                    if not save_review(
                        session_id, review_, scores,
                        overall.get("new_weak_points", []), overall,
                        user_id=user_id, evaluation_token=evaluation_token,
                    ):
                        raise _SyncClaimLost("evaluation generation changed before drill persistence")

                    if metric_writer is not None and answered_scores:
                        try:
                            metric_saved = metric_writer(
                                session_id, user_id, topic, "answer_eval",
                                faithfulness=(gen_metrics.faithfulness if gen_metrics else None),
                                answer_relevance=(gen_metrics.answer_relevance if gen_metrics else None),
                                answer_correctness=(gen_metrics.answer_correctness if gen_metrics else None),
                                chunk_count=len(answered_scores),
                                detail=metric_detail,
                                evaluation_token=evaluation_token,
                            )
                            if not metric_saved:
                                raise _SyncClaimLost(
                                    "evaluation generation changed before RAG metrics persistence"
                                )
                        except _SyncClaimLost:
                            raise
                        except Exception as exc:
                            import logging
                            logging.getLogger("uvicorn").warning(
                                "RAG eval metrics persistence failed: %s", exc,
                            )

                    claim_token = None
                    if not already_synced and _has_valid_side_effect_score(scores):
                        claim_token = await asyncio.to_thread(
                            try_claim_session_sync, session_id, user_id=user_id,
                            evaluation_token=evaluation_token,
                        )

                    if claim_token:
                        try:
                            synced = await _apply_drill_side_effects(
                                session_id, topic, questions, answers, scores,
                                overall, user_id, claim_token,
                            )
                        except Exception as exc:
                            await asyncio.to_thread(
                                release_session_sync_claim, session_id,
                                user_id=user_id, claim_token=claim_token,
                            )
                            raise RuntimeError(
                                f"drill side-effects remain pending: {exc}"
                            ) from exc
                        if not synced:
                            raise _SyncClaimLost(
                                f"drill sync claim lost: {session_id}"
                            )
                    elif not already_synced and _has_valid_side_effect_score(scores):
                        latest = await asyncio.to_thread(
                            get_session, session_id, user_id=user_id,
                        )
                        latest_meta = ((latest or {}).get("meta") or {})
                        if not latest_meta.get("synced_at"):
                            raise _SyncClaimLost(
                                "could not claim drill side-effects for this evaluation"
                            )
                        if latest_meta.get("evaluation_claim_token") != evaluation_token:
                            raise _SyncClaimLost(
                                "drill evaluation generation was superseded by the sync winner"
                            )
                    if not await _evaluation_generation_is_current(
                        session_id, user_id, evaluation_token,
                    ):
                        raise _SyncClaimLost(
                            "drill evaluation generation changed before completion"
                        )
                    del_live(drill_sessions, session_id, user_id)
                    return review_
                except Exception as e:
                    import logging
                    logging.getLogger("uvicorn").error(f"Drill result persistence failed: {e}")
                    raise

            try:
                review = await _finish_despite_cancellation(_persist_drill())
            except Exception:
                yield sse_event({
                    "type": "error",
                    "message": "Evaluation finished, but the result could not be saved. Please retry.",
                })
                yield sse_event({"type": "done"})
                return

            if not await _evaluation_generation_is_current(
                session_id, user_id, evaluation_token,
            ):
                yield sse_event({
                    "type": "error",
                    "message": "A newer evaluation replaced this result. Please reload.",
                })
                yield sse_event({"type": "done"})
                return

            result = {
                "session_id": session_id,
                "mode": "topic_drill",
                "review": review,
                "scores": scores,
                "overall": overall,
            }
            yield sse_event({"type": "complete", "data": result})
            yield sse_event({"type": "done"})

        return streaming_response(_release_eval_claim_after(
            _stream_drill(), session_id, user_id, evaluation_token,
        ))

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
        evaluation_token = await asyncio.to_thread(
            try_claim_session_evaluation, session_id, user_id=user_id,
        )
        if not evaluation_token:
            latest = await asyncio.to_thread(
                get_session, session_id, user_id=user_id,
            )
            latest_meta = (latest or {}).get("meta", {}) or {}
            if not latest_meta.get("synced_at") and (
                latest_meta.get("sync_pending_at")
                or latest_meta.get("sync_steps")
            ):
                raise HTTPException(
                    409,
                    "Previous evaluation side-effects are pending; sync them "
                    "before re-evaluating this session.",
                )
            raise HTTPException(409, "Evaluation is already in progress for this session.")
        try:
            if not save_drill_answers(
                session_id, answers, user_id=user_id,
                evaluation_token=evaluation_token,
            ):
                raise _SyncClaimLost(
                    "evaluation generation changed before answers were saved"
                )
        except Exception:
            await asyncio.to_thread(
                release_session_evaluation_claim, session_id,
                user_id=user_id, claim_token=evaluation_token,
            )
            raise

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

            scores = _normalize_scores(eval_result.get("scores", []), questions)
            overall = _normalize_overall(eval_result.get("overall", {}))

            # Shielded persistence — see _persist_drill. Survives a client disconnect.
            async def _persist_job_prep():
                try:
                    review_ = format_job_prep_review(questions, answers, scores, overall, meta)
                    if not save_review(
                        session_id, review_, scores,
                        overall.get("new_weak_points", []), overall,
                        user_id=user_id, evaluation_token=evaluation_token,
                    ):
                        raise _SyncClaimLost("evaluation generation changed before JD persistence")

                    claim_token = None
                    jd_target_topics = None
                    if not already_synced and _has_valid_side_effect_score(scores):
                        jd_target_topics = await asyncio.to_thread(
                            _match_jd_to_topics, meta, user_id,
                        )
                        claim_token = await asyncio.to_thread(
                            try_claim_session_sync, session_id, user_id=user_id,
                            evaluation_token=evaluation_token,
                            target_group="knowledge",
                            target_topics=jd_target_topics,
                        )

                    if claim_token:
                        frozen_jd_targets = await asyncio.to_thread(
                            session_sync_targets,
                            session_id,
                            "knowledge",
                            user_id=user_id,
                            claim_token=claim_token,
                        )
                        try:
                            synced = await _apply_job_prep_side_effects(
                                session_id, questions, answers, scores, overall,
                                meta, user_id, claim_token,
                                target_topics=frozen_jd_targets,
                            )
                        except Exception as exc:
                            await asyncio.to_thread(
                                release_session_sync_claim, session_id,
                                user_id=user_id, claim_token=claim_token,
                            )
                            raise RuntimeError(
                                f"JD prep side-effects remain pending: {exc}"
                            ) from exc
                        if not synced:
                            raise _SyncClaimLost(
                                f"JD prep sync claim lost: {session_id}"
                            )
                    elif not already_synced and _has_valid_side_effect_score(scores):
                        latest = await asyncio.to_thread(
                            get_session, session_id, user_id=user_id,
                        )
                        latest_meta = ((latest or {}).get("meta") or {})
                        if not latest_meta.get("synced_at"):
                            raise _SyncClaimLost(
                                "could not claim JD prep side-effects for this evaluation"
                            )
                        if latest_meta.get("evaluation_claim_token") != evaluation_token:
                            raise _SyncClaimLost(
                                "JD prep evaluation generation was superseded by the sync winner"
                            )
                    if not await _evaluation_generation_is_current(
                        session_id, user_id, evaluation_token,
                    ):
                        raise _SyncClaimLost(
                            "JD prep evaluation generation changed before completion"
                        )
                    del_live(job_prep_sessions, session_id, user_id)
                    return review_
                except Exception as e:
                    import logging
                    logging.getLogger("uvicorn").error(f"JD prep persistence failed: {e}")
                    raise

            try:
                review = await _finish_despite_cancellation(_persist_job_prep())
            except Exception:
                yield sse_event({
                    "type": "error",
                    "message": "Evaluation finished, but the result could not be saved. Please retry.",
                })
                yield sse_event({"type": "done"})
                return

            if not await _evaluation_generation_is_current(
                session_id, user_id, evaluation_token,
            ):
                yield sse_event({
                    "type": "error",
                    "message": "A newer evaluation replaced this result. Please reload.",
                })
                yield sse_event({"type": "done"})
                return

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

        return streaming_response(_release_eval_claim_after(
            _stream_job_prep(), session_id, user_id, evaluation_token,
        ))

    # -- Resume mode --
    entry = _get_resume_graph(session_id, user_id)
    if entry is None:
        raise HTTPException(404, "Session not found.")
    if entry.get("user_id") != user_id:
        raise HTTPException(403, "Access denied.")

    graph = entry["graph"]
    config = entry["config"]

    evaluation_token = await asyncio.to_thread(
        try_claim_session_evaluation, session_id, user_id=user_id,
    )
    if not evaluation_token:
        latest = await asyncio.to_thread(
            get_session, session_id, user_id=user_id,
        )
        latest_transcript = (latest or {}).get("transcript") or []
        if latest_transcript and latest_transcript[-1].get("role") == "user":
            raise HTTPException(
                409,
                "The previous interview reply must be recovered before evaluation.",
            )
        raise HTTPException(409, "Evaluation is already in progress for this session.")

    async def _stream_resume_unlocked():
        state = graph.get_state(config)
        messages = state.values.get("messages", [])
        scores = _normalize_scores(state.values.get("scores", []))
        weak_points = state.values.get("weak_points", [])
        eval_history = state.values.get("eval_history", [])
        topic_name = state.values.get("topic_name", entry.get("topic"))

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
            claim_token = None
            try:
                already_synced = bool(existing and (existing.get("meta") or {}).get("synced_at"))
                resume_target_topics = await asyncio.to_thread(
                    _match_resume_to_topics, messages, user_id,
                )
                if not already_synced:
                    claim_token = await asyncio.to_thread(
                        try_claim_session_sync, session_id, user_id=user_id,
                        evaluation_token=evaluation_token,
                        target_group="knowledge",
                        target_topics=resume_target_topics,
                    )
                    if not claim_token:
                        raise _SyncClaimLost(
                            "resume side-effects are already claimed by another worker"
                        )
                frozen_resume_targets = []
                if claim_token:
                    frozen_resume_targets = await asyncio.to_thread(
                        session_sync_targets,
                        session_id,
                        "knowledge",
                        user_id=user_id,
                        claim_token=claim_token,
                    )

                extraction_ = {}
                resume_overall = (existing or {}).get("overall", {}) or {}
                steps = await asyncio.to_thread(
                    session_sync_steps, session_id, user_id=user_id,
                )
                profile_pending = bool(claim_token and "profile" not in steps)
                if profile_pending:
                    extraction_ = await update_profile_after_interview(
                        mode=entry["mode"].value,
                        topic=entry.get("topic"),
                        messages=messages,
                        user_id=user_id,
                        scores=scores,
                        operation_id=_sync_operation_id(session_id, "profile"),
                    )

                    resume_overall = {}
                    if extraction_.get("dimension_scores") is not None:
                        resume_overall["dimension_scores"] = extraction_["dimension_scores"]
                    if extraction_.get("avg_score") is not None:
                        resume_overall["avg_score"] = extraction_["avg_score"]

                    # The profile mutation already happened. Persist both its
                    # completion marker and reusable extraction before review,
                    # so a review write failure cannot apply the profile twice.
                    await _mark_sync_step(
                        session_id, "profile", user_id, claim_token,
                        result={
                            "extraction": extraction_,
                            "overall": resume_overall,
                        },
                    )
                elif "profile" in steps:
                    profile_result = await asyncio.to_thread(
                        session_sync_step_result, session_id, "profile",
                        user_id=user_id,
                    )
                    if profile_result:
                        stored_extraction = profile_result.get("extraction")
                        stored_overall = profile_result.get("overall")
                        if isinstance(stored_extraction, dict):
                            extraction_ = stored_extraction
                        if isinstance(stored_overall, dict):
                            resume_overall = stored_overall

                # Always persist the generated review. Only the profile / knowledge
                # side-effects are claim-gated; on duplicate calls we preserve the
                # previous overall metrics instead of wiping them with an empty extraction.
                if not save_review(
                    session_id, review_text, scores, weak_points,
                    overall=resume_overall, user_id=user_id,
                    evaluation_token=evaluation_token,
                ):
                    raise _SyncClaimLost("evaluation generation changed before resume persistence")

                if claim_token:
                    from backend.knowledge_evolution import extract_and_writeback, collect_high_freq
                    resume_topics = frozen_resume_targets
                    if resume_topics and eval_history:
                        rows = [e for e in eval_history if e.get("question")]
                        resume_qs = [{"question": e.get("question", "")} for e in rows]
                        resume_scores = [
                            {"score": e.get("score"), "assessment": e.get("assessment", "")}
                            for e in rows
                        ]
                        resume_answers = [e.get("answer", "") for e in rows]
                        for target_topic in resume_topics:
                            extract_step = f"knowledge_extract:{target_topic}"
                            freq_step = f"high_freq:{target_topic}"
                            if await _skip_deleted_topic_steps(
                                session_id,
                                target_topic,
                                steps,
                                user_id,
                                claim_token,
                            ):
                                continue
                            if extract_step not in steps:
                                ok = await extract_and_writeback(
                                    target_topic, resume_qs, resume_answers,
                                    resume_scores, user_id,
                                    operation_id=_sync_operation_id(
                                        session_id, extract_step,
                                    ),
                                )
                                if ok is False:
                                    raise RuntimeError(
                                        f"knowledge extraction failed for {target_topic}"
                                    )
                                await _mark_sync_step(
                                    session_id, extract_step, user_id, claim_token,
                                )
                            if freq_step not in steps:
                                ok = await collect_high_freq(
                                    target_topic, resume_qs, resume_scores, user_id,
                                    operation_id=_sync_operation_id(
                                        session_id, freq_step,
                                    ),
                                )
                                if ok is False:
                                    raise RuntimeError(
                                        f"high-frequency collection failed for {target_topic}"
                                    )
                                await _mark_sync_step(
                                    session_id, freq_step, user_id, claim_token,
                                )
                    marked = await asyncio.to_thread(
                        mark_session_synced, session_id,
                        user_id=user_id, claim_token=claim_token,
                    )
                    if not marked:
                        raise _SyncClaimLost("resume sync claim lost before completion")
                    await _confirm_profile_operations(session_id, user_id)

                # Keep the checkpoint/live-session recoverable until every
                # target side-effect and the terminal sync marker are durable.
                if not await _evaluation_generation_is_current(
                    session_id, user_id, evaluation_token,
                ):
                    raise _SyncClaimLost(
                        "resume evaluation generation changed before completion"
                    )
                del_live(graphs, session_id, user_id)
                return extraction_
            except Exception as e:
                import logging
                logging.getLogger("uvicorn").error(f"Resume persistence failed: {e}")
                if claim_token:
                    await asyncio.to_thread(
                        release_session_sync_claim, session_id,
                        user_id=user_id, claim_token=claim_token,
                    )
                raise

        try:
            extraction = await _finish_despite_cancellation(_persist_resume())
        except Exception:
            yield sse_event({
                "type": "error",
                "message": "Review finished, but the result could not be saved. Please retry.",
            })
            yield sse_event({"type": "done"})
            return

        if not await _evaluation_generation_is_current(
            session_id, user_id, evaluation_token,
        ):
            yield sse_event({
                "type": "error",
                "message": "A newer evaluation replaced this result. Please reload.",
            })
            yield sse_event({"type": "done"})
            return

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

    async def _stream_resume():
        async with _get_resume_session_lock(user_id, session_id):
            async for item in _stream_resume_unlocked():
                yield item

    return streaming_response(_release_eval_claim_after(
        _stream_resume(), session_id, user_id, evaluation_token,
    ))


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

    scores = _normalize_scores(session.get("scores") or [], session.get("questions") or [])
    has_valid = _has_valid_side_effect_score(scores)
    if not has_valid:
        raise HTTPException(400, "该会话没有有效评分，请先重新评估再同步。")

    jd_target_candidates = None
    if mode == "jd_prep":
        jd_target_candidates = await asyncio.to_thread(
            _match_jd_to_topics, session.get("meta", {}) or {}, user_id,
        )
    claimed = await asyncio.to_thread(
        try_claim_session_sync,
        session_id,
        user_id=user_id,
        target_group="knowledge" if mode == "jd_prep" else None,
        target_topics=jd_target_candidates,
    )
    if not claimed:
        latest = await asyncio.to_thread(get_session, session_id, user_id=user_id)
        meta = (latest or {}).get("meta", {}) or {}
        if meta.get("synced_at"):
            return {"status": "already_synced", "synced_at": meta["synced_at"]}
        return {"status": "sync_in_progress", "claimed_at": meta.get("sync_claimed_at")}

    # The pre-check above is only advisory. A stale evaluation worker may have
    # committed a newer review/scores payload immediately before this claim
    # atomically fenced it, so every side-effect input must come from a fresh
    # post-claim snapshot.
    session = await asyncio.to_thread(get_session, session_id, user_id=user_id)
    if not session:
        await asyncio.to_thread(
            release_session_sync_claim, session_id,
            user_id=user_id, claim_token=claimed,
        )
        raise HTTPException(404, "Session not found.")
    fresh_meta = session.get("meta", {}) or {}
    if fresh_meta.get("synced_at"):
        return {
            "status": "already_synced",
            "synced_at": fresh_meta["synced_at"],
        }
    if fresh_meta.get("sync_claim_token") != claimed:
        return {
            "status": "sync_in_progress",
            "claimed_at": fresh_meta.get("sync_claimed_at"),
        }
    mode = session.get("mode")
    if mode not in ("topic_drill", "jd_prep"):
        await asyncio.to_thread(
            abort_session_sync_claim, session_id,
            user_id=user_id, claim_token=claimed,
        )
        raise HTTPException(400, "仅训练 / JD 备面会话支持同步到画像。")

    questions = session.get("questions") or []
    scores = _normalize_scores(session.get("scores") or [], questions)
    if not _has_valid_side_effect_score(scores):
        await asyncio.to_thread(
            abort_session_sync_claim, session_id,
            user_id=user_id, claim_token=claimed,
        )
        raise HTTPException(400, "该会话没有有效评分，请先重新评估再同步。")
    overall = _normalize_overall(session.get("overall") or {})
    answers = _answers_from_transcript(questions, session.get("transcript", []))

    try:
        if mode == "topic_drill":
            synced = await _apply_drill_side_effects(
                session_id, session.get("topic"), questions, answers, scores,
                overall, user_id, claimed,
            )
        else:
            frozen_jd_targets = await asyncio.to_thread(
                session_sync_targets,
                session_id,
                "knowledge",
                user_id=user_id,
                claim_token=claimed,
            )
            synced = await _apply_job_prep_side_effects(
                session_id, questions, answers, scores, overall,
                session.get("meta", {}) or {}, user_id, claimed,
                target_topics=frozen_jd_targets,
            )
    except Exception as exc:
        await asyncio.to_thread(
            release_session_sync_claim, session_id,
            user_id=user_id, claim_token=claimed,
        )
        raise HTTPException(503, f"同步未完成，可安全重试：{exc}") from exc

    latest = await asyncio.to_thread(get_session, session_id, user_id=user_id)
    synced_at = ((latest or {}).get("meta") or {}).get("synced_at")
    if not synced or not synced_at:
        return {"status": "sync_in_progress"}
    if mode == "topic_drill":
        del_live(drill_sessions, session_id, user_id)
    else:
        del_live(job_prep_sessions, session_id, user_id)
    return {"status": "synced", "synced_at": synced_at}


@router.post("/interview/reference-answer")
async def generate_reference_answer(
    body: ReferenceAnswerRequest,
    user_id: str = Depends(get_current_user),
):
    topic = body.topic
    question = body.question
    session_id = body.session_id
    question_id = body.question_id
    force = body.force
    mode = body.mode

    from backend.storage.sessions import get_reference_answer, save_reference_answer

    cache_key_suffix = "_hint" if mode == "hint" else ""
    cache_qid = f"{question_id}{cache_key_suffix}" if question_id else None

    if session_id and cache_qid and not force:
        cached = get_reference_answer(session_id, cache_qid, user_id=user_id)
        if cached:
            return {"reference_answer": cached, "cached": True, "mode": mode}

    from backend.indexer import safe_retrieve_topic_context
    from backend.prompts.interviewer import REFERENCE_ANSWER_PROMPT, HINT_PROMPT

    topics = load_topics(user_id)
    topic_name = topics.get(topic, {}).get("name", topic)

    refs = await safe_retrieve_topic_context(topic, question, user_id, top_k=3, timeout=60.0,
                                             build_if_missing=False)
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
            elif kind == "error":
                return  # LLM failed — don't cache an empty answer
            else:
                answer = value.strip()

        if not answer:
            yield sse_event({"type": "error", "message": "生成内容为空，请稍后重试。"})
            return

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
    """Match JD prep metadata to the user's knowledge topics.

    Reads ``jd_excerpt`` — the key the JD session actually persists (see
    ``routers/job_prep.py`` where meta is built) — alongside ``position``. An
    earlier version read a non-existent ``jd_text`` key, so matching silently
    collapsed to the position string alone and the knowledge / high-freq
    writeback leg almost never fired. No fallback-to-arbitrary-topics on a miss:
    writeback pushes the user's mistakes into a topic's knowledge base, so an
    empty match must stay empty rather than pollute unrelated topics.
    """
    topics = load_topics(user_id)
    if not topics:
        return []
    jd_text = (
        str(meta.get("jd_excerpt", "")) + " " + str(meta.get("position", ""))
    ).lower()
    matched = []
    for key, info in topics.items():
        name = info.get("name", key).lower()
        if name in jd_text or key.lower() in jd_text:
            matched.append(key)
    return matched[:3]


# ── Profile update helpers ──

async def _update_drill_profile(topic: str, overall: dict, scores: list,
                                total_questions: int, user_id: str,
                                operation_id: str | None = None):
    valid = []
    for s in scores:
        if s.get("skipped"):
            continue
        score = _numeric_score(s.get("score"))
        if score is not None:
            valid.append((score, float(s.get("difficulty", 3))))
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
        answer_count=len(valid),
        session_weight=session_weight,
        operation_id=operation_id,
    )


async def _update_job_prep_profile(overall: dict, scores: list, total_questions: int,
                                   meta: dict, user_id: str,
                                   operation_id: str | None = None):
    valid = []
    for s in scores:
        if s.get("skipped"):
            continue
        score = _numeric_score(s.get("score"))
        if score is not None:
            valid.append(score)

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
        operation_id=operation_id,
    )
