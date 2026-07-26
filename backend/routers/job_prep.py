"""JD-targeted prep routes — preview and start."""

import asyncio

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

        if not preview:
            got_preview = False
            async for kind, value in stream_generate_job_prep_preview(
                jd_text, user_id,
                company=req.company, position=req.position, use_resume=req.use_resume,
            ):
                if kind == "sse":
                    yield value
                else:
                    preview = value
                    got_preview = True
            if not got_preview:
                return

        questions = None
        async for kind, value in stream_generate_job_prep_questions(
            jd_text, preview, user_id, use_resume=req.use_resume,
        ):
            if kind == "sse":
                yield value
            else:
                questions = value
        if questions is None:
            return

        session_id = new_session_id()
        meta = {
            "company": preview.get("company") or (req.company or "").strip(),
            "position": preview.get("position") or (req.position or "").strip() or "JD 备面",
            "jd_excerpt": jd_text[:1500],
            "use_resume": req.use_resume,
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
