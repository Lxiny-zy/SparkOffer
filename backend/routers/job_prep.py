"""JD-targeted prep routes — preview and start."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Depends

from backend.models import (
    JobPrepPreviewRequest, JobPrepStartRequest, InterviewMode,
)
from backend.storage.sessions import create_session, new_session_id
from backend.graphs.job_prep import (
    stream_generate_job_prep_preview, stream_generate_job_prep_questions,
)
from backend.live_store import job_prep_sessions, save_live
from backend.auth import get_current_user
from backend.utils.sse_helpers import streaming_response, sse_event

router = APIRouter(prefix="/api")


@router.post("/job-prep/preview")
async def job_prep_preview(req: JobPrepPreviewRequest, user_id: str = Depends(get_current_user)):
    jd_text = req.jd_text.strip()
    if len(jd_text) < 50:
        raise HTTPException(400, "JD 内容太短，无法分析。")

    async def _gen():
        async for kind, value in stream_generate_job_prep_preview(
            jd_text, user_id,
            company=req.company, position=req.position, use_resume=req.use_resume,
        ):
            if kind == "sse":
                yield value
            else:
                yield sse_event({"type": "complete", "data": {"preview": value}})
                yield sse_event({"type": "done"})

    return streaming_response(_gen())


@router.post("/job-prep/start")
async def job_prep_start(req: JobPrepStartRequest, user_id: str = Depends(get_current_user)):
    jd_text = req.jd_text.strip()
    if len(jd_text) < 50:
        raise HTTPException(400, "JD 内容太短，无法生成训练。")

    async def _gen():
        preview = req.preview_data if isinstance(req.preview_data, dict) else None
        # Allocate the id up front so retrieval metrics can be attributed to this
        # session. The row itself is still only created once questions succeed,
        # so an aborted generation leaves no session behind.
        session_id = new_session_id()

        if not preview:
            got_preview = False
            preview_error = False
            async for kind, value in stream_generate_job_prep_preview(
                jd_text, user_id,
                company=req.company, position=req.position, use_resume=req.use_resume,
            ):
                if kind == "sse":
                    yield value
                    if isinstance(value, str) and value.startswith("data: "):
                        try:
                            preview_error |= json.loads(value[6:].strip()).get("type") == "error"
                        except (TypeError, json.JSONDecodeError):
                            pass
                else:
                    preview = value
                    got_preview = True
            if not got_preview:
                if not preview_error:
                    yield sse_event({"type": "error", "message": "JD 分析未返回有效结果，请稍后重试"})
                return

        questions = None
        questions_error = False
        async for kind, value in stream_generate_job_prep_questions(
            jd_text, preview, user_id, use_resume=req.use_resume,
            session_id=session_id,
        ):
            if kind == "sse":
                yield value
                if isinstance(value, str) and value.startswith("data: "):
                    try:
                        questions_error |= json.loads(value[6:].strip()).get("type") == "error"
                    except (TypeError, json.JSONDecodeError):
                        pass
            else:
                questions = value
        if questions is None:
            if not questions_error:
                yield sse_event({"type": "error", "message": "JD 出题未返回有效结果，请稍后重试"})
            return

        # Freeze the matched knowledge topics on the session. JD prep spans
        # several topics, so the sessions.topic column stays NULL; downstream
        # consumers (reference-answer / hint retrieval) read meta.topics.
        from backend.graphs.job_prep import _match_jd_topics
        jd_topics = await asyncio.to_thread(_match_jd_topics, jd_text, user_id)
        meta = {
            "company": preview.get("company") or (req.company or "").strip(),
            "position": preview.get("position") or (req.position or "").strip() or "JD 备面",
            "jd_excerpt": jd_text[:1500],
            "use_resume": req.use_resume,
            "resume_used": bool(
                (preview.get("resume_alignment") or {}).get("resume_used")
            ),
            "topics": jd_topics,
            "preview": preview,
        }

        await asyncio.to_thread(
            create_session, session_id, InterviewMode.JD_PREP.value,
            questions=questions, meta=meta, user_id=user_id,
        )
        await asyncio.to_thread(save_live, job_prep_sessions, session_id, "job_prep", user_id, {
            "questions": questions, "preview": preview, "meta": meta, "user_id": user_id,
        })

        yield sse_event({"type": "complete", "data": {
            "session_id": session_id,
            "mode": InterviewMode.JD_PREP.value,
            "questions": questions,
            "preview": preview,
            "company": meta["company"],
            "position": meta["position"],
            "meta": meta,
        }})
        yield sse_event({"type": "done"})

    return streaming_response(_gen())
