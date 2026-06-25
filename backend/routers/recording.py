"""Recording transcription & analysis routes."""
import asyncio
import uuid

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from langchain_core.messages import HumanMessage

from backend.models import RecordingAnalyzeRequest
from backend.memory import llm_update_profile
from backend.storage.sessions import create_session, save_review, save_drill_answers
from backend.formatters import format_drill_review, format_solo_review
from backend.auth import get_current_user

router = APIRouter(prefix="/api")

MAX_AUDIO_BYTES = 50 * 1024 * 1024    # 50MB — short interview-answer clips


@router.post("/recording/transcribe")
async def recording_transcribe(
    file: UploadFile = File(...),
    mode: str = Form("dual"),
    user_id: str = Depends(get_current_user),
):
    # Read with a size cap so an oversized upload can't balloon memory and then
    # pin a to_thread worker inside the transcriber. Mirrors resume.py /transcribe.
    parts = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_AUDIO_BYTES:
            raise HTTPException(413, f"音频过大（>{MAX_AUDIO_BYTES // (1024*1024)}MB）")
        parts.append(chunk)
    audio_bytes = b"".join(parts)
    if not audio_bytes:
        raise HTTPException(400, "Empty audio file.")

    suffix = "." + (file.filename or "audio.webm").rsplit(".", 1)[-1]

    try:
        from backend.transcribe import transcribe_audio
        text = await asyncio.to_thread(transcribe_audio, audio_bytes, suffix=suffix)
        return {"transcript": text, "segments": []}
    except Exception as e:
        raise HTTPException(500, f"Transcription failed: {e}")


@router.post("/recording/analyze")
async def recording_analyze(req: RecordingAnalyzeRequest, user_id: str = Depends(get_current_user)):
    from backend.utils.sse_helpers import streaming_response

    session_id = str(uuid.uuid4())

    if req.recording_mode == "dual":
        return streaming_response(_stream_analyze_dual(req, session_id, user_id))
    else:
        return streaming_response(_stream_analyze_solo(req, session_id, user_id))


async def _stream_analyze_dual(req: RecordingAnalyzeRequest, session_id: str, user_id: str):
    from backend.graphs.topic_drill import _parse_json_response
    from backend.prompts.recording import RECORDING_STRUCTURE_PROMPT, RECORDING_DUAL_EVAL_PROMPT
    from langchain_core.messages import SystemMessage
    from backend.utils.sse_helpers import stream_llm_sse, sse_event

    # Step 1: structure extraction
    structure_prompt = RECORDING_STRUCTURE_PROMPT.format(transcript=req.transcript[:8000])
    structure_messages = [
        SystemMessage(content="你是面试记录分析引擎。只返回 JSON，不要其他内容。"),
        HumanMessage(content=structure_prompt),
    ]

    structure_text = ""
    async for kind, value in stream_llm_sse(structure_messages, progress_prefix="正在分析录音结构"):
        if kind == "sse":
            yield value
        else:
            structure_text = value

    try:
        structured = _parse_json_response(structure_text)
    except Exception:
        yield sse_event({"type": "error", "message": "录音结构化失败，LLM 返回格式异常。请重试。"})
        return

    qa_pairs = structured.get("qa_pairs", [])
    if not qa_pairs:
        yield sse_event({"type": "error", "message": "未能从录音中提取出有效的问答对。请检查转写文本。"})
        return

    questions = []
    answers = []
    for pair in qa_pairs:
        qid = pair.get("id", len(questions) + 1)
        questions.append({
            "id": qid, "question": pair["question"],
            "difficulty": 3, "focus_area": pair.get("focus_area", ""),
        })
        answers.append({"question_id": qid, "answer": pair.get("answer", "")})

    create_session(session_id, mode="recording", questions=questions, user_id=user_id)

    # Step 2: evaluation
    qa_lines = []
    for q, a in zip(questions, answers):
        qa_lines.append(
            f"### Q{q['id']} ({q.get('focus_area', '')})\n"
            f"**题目**: {q['question']}\n**回答**: {a['answer']}"
        )

    eval_messages = [
        SystemMessage(content="你是面试评估引擎。只返回 JSON，不要其他内容。"),
        HumanMessage(content=RECORDING_DUAL_EVAL_PROMPT.format(qa_pairs="\n\n".join(qa_lines))),
    ]

    eval_text = ""
    async for kind, value in stream_llm_sse(eval_messages, progress_prefix="正在评估回答质量"):
        if kind == "sse":
            yield value
        else:
            eval_text = value

    try:
        eval_result = _parse_json_response(eval_text)
    except Exception:
        yield sse_event({"type": "error", "message": "评估失败，LLM 返回格式异常。请重试。"})
        return

    scores = eval_result.get("scores", [])
    overall = eval_result.get("overall", {})
    for s in scores:
        s.setdefault("difficulty", 3)

    review = format_drill_review(questions, answers, scores, overall)
    save_drill_answers(session_id, answers, user_id=user_id)
    save_review(session_id, review, scores, overall.get("new_weak_points", []), overall, user_id=user_id)
    await _update_recording_profile(overall, scores, len(questions), user_id)

    yield sse_event({"type": "complete", "data": {
        "session_id": session_id, "mode": "recording", "recording_mode": "dual",
        "review": review, "scores": scores, "overall": overall,
        "questions": questions, "answers": answers,
    }})
    yield sse_event({"type": "done"})


async def _stream_analyze_solo(req: RecordingAnalyzeRequest, session_id: str, user_id: str):
    from backend.graphs.topic_drill import _parse_json_response
    from backend.prompts.recording import RECORDING_SOLO_EVAL_PROMPT
    from langchain_core.messages import SystemMessage
    from backend.utils.sse_helpers import stream_llm_sse, sse_event

    create_session(session_id, mode="recording", user_id=user_id)

    eval_messages = [
        SystemMessage(content="你是录音评估引擎。只返回 JSON，不要其他内容。"),
        HumanMessage(content=RECORDING_SOLO_EVAL_PROMPT.format(transcript=req.transcript[:8000])),
    ]

    eval_text = ""
    async for kind, value in stream_llm_sse(eval_messages, progress_prefix="正在评估录音内容"):
        if kind == "sse":
            yield value
        else:
            eval_text = value

    try:
        eval_result = _parse_json_response(eval_text)
    except Exception:
        yield sse_event({"type": "error", "message": "评估失败，LLM 返回格式异常。请重试。"})
        return

    topics_covered = eval_result.get("topics_covered", [])
    overall = eval_result.get("overall", {})

    review = format_solo_review(topics_covered, overall)
    scores = [
        {"question_id": t.get("id", i + 1), "score": t.get("score"), "difficulty": 3}
        for i, t in enumerate(topics_covered)
    ]

    save_review(session_id, review, scores, overall.get("new_weak_points", []), overall, user_id=user_id)
    await _update_recording_profile(overall, scores, max(len(topics_covered), 1), user_id)

    yield sse_event({"type": "complete", "data": {
        "session_id": session_id, "mode": "recording", "recording_mode": "solo",
        "review": review, "topics_covered": topics_covered, "overall": overall,
    }})
    yield sse_event({"type": "done"})


async def _update_recording_profile(overall: dict, scores: list, total_items: int, user_id: str):
    valid = []
    for s in scores:
        try:
            valid.append(float(s["score"]))
        except (TypeError, ValueError, KeyError):
            pass

    await llm_update_profile(
        mode="recording", topic=None,
        new_weak_points=overall.get("new_weak_points", []),
        new_strong_points=overall.get("new_strong_points", []),
        topic_mastery={},
        communication=overall.get("communication_observations", {}),
        user_id=user_id,
        thinking_patterns=overall.get("thinking_patterns"),
        session_summary=overall.get("summary", ""),
        avg_score=overall.get("avg_score"),
        answer_count=len(valid),
        session_weight=0.3,
    )
