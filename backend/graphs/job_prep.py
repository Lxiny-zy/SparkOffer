"""JD 定向备面服务."""
import hashlib
import json
import logging
from collections.abc import AsyncGenerator

from langchain_core.messages import HumanMessage, SystemMessage

from backend.config import settings
from backend.graphs.topic_drill import _parse_json_response
from backend.indexer import query_resume, gather_topic_contexts, load_topics
from backend.llm_provider import get_langchain_llm
from backend.memory import get_profile_summary
from backend.utils.sse_helpers import iter_llm_stream, chunk_text
from backend.utils.stream_parser import extract_complete_objects
from backend.redis_cache import get_cache
from backend.prompts.job_prep import (
    JOB_PREP_EVAL_PROMPT,
    JOB_PREP_PREVIEW_PROMPT,
    JOB_PREP_QUESTION_GEN_PROMPT,
)

logger = logging.getLogger("uvicorn")


_JD_KNOWLEDGE_TTL = 600  # seconds; a prep flow (preview→questions→eval) is short


def _get_knowledge_for_jd(jd_text: str, user_id: str) -> str:
    """Retrieve knowledge base context matching JD keywords.

    Cached by (user, jd) for the duration of a prep flow: the preview, question
    and eval steps all call this with the same jd_text, so without the cache the
    multi-topic retrieval would run 2–3× per session. On a miss the matched topics
    are retrieved concurrently (was: serial, one max_workers=1 pool per topic).
    """
    try:
        topics = load_topics(user_id)
        if not topics:
            return ""

        cache = get_cache()
        digest = hashlib.sha256(jd_text.encode("utf-8", errors="ignore")).hexdigest()[:16]
        ck = f"jdknow:{user_id}:{digest}"
        cached = cache.get_json(ck)
        if cached is not None:
            return cached

        jd_lower = jd_text.lower()
        matched = []
        for key, info in topics.items():
            name = info.get("name", key).lower()
            if name in jd_lower or key.lower() in jd_lower:
                matched.append(key)
        if not matched:
            matched = list(topics.keys())[:3]

        per_topic = gather_topic_contexts(
            [(k, "核心知识点 面试常见问题") for k in matched[:5]], user_id, top_k=2,
        )
        chunks = [c for topic_chunks in per_topic for c in topic_chunks]
        if chunks:
            # Token-budget knowledge (~40% of the input window) instead of the old
            # per-chunk [:500] + total [:3000] two-stage char starvation. Cached below
            # and shared by the preview / question-gen / eval prompts.
            from backend.context_assembler import ContextBudget, Section, resolve_input_budget
            kb_budget = max(1000, int(resolve_input_budget() * 0.4))
            result = ContextBudget(kb_budget).pack(
                [Section("kb", "\n\n---\n\n".join(chunks), priority=1, min_tokens=200)]
            ).get("kb")
        else:
            result = ""
        cache.set_json(ck, result, _JD_KNOWLEDGE_TTL)
        return result
    except Exception as e:
        logger.warning("Knowledge retrieval for JD prep failed: %s", e)
        return ""


def _has_resume(user_id: str) -> bool:
    resume_dir = settings.user_resume_path(user_id)
    return resume_dir.exists() and any(
        f.suffix.lower() == ".pdf" for f in resume_dir.iterdir() if f.is_file()
    )


def _get_resume_context(user_id: str, use_resume: bool) -> tuple[str, bool]:
    if not use_resume or not _has_resume(user_id):
        return "未启用简历联动", False

    try:
        resume_context = query_resume(
            "总结候选人的项目经历、技术栈、AI/后端/工程化相关实践，以及最适合拿来面这个岗位的经历",
            user_id,
            top_k=4,
        )
        # Token-budget the resume context (~30% of the input window) instead of [:5000].
        from backend.context_assembler import ContextBudget, Section, resolve_input_budget
        fit = ContextBudget(max(1000, int(resolve_input_budget() * 0.3))).pack(
            [Section("resume", str(resume_context), priority=1, min_tokens=200)]
        ).get("resume")
        return fit, True
    except Exception as exc:
        logger.warning(f"Failed to load resume context for JD prep: {exc}")
        return "简历检索失败，本次按无简历联动处理", False


def _normalize_preview(
    data: dict,
    *,
    company: str | None,
    position: str | None,
    jd_text: str,
    resume_used: bool,
) -> dict:
    resume_alignment = data.get("resume_alignment") or {}

    preview = {
        "company": (company or data.get("company") or "").strip(),
        "position": (position or data.get("position") or "").strip(),
        "role_summary": data.get("role_summary", "").strip(),
        "focus_areas": data.get("focus_areas") or [],
        "likely_question_groups": data.get("likely_question_groups") or [],
        "resume_alignment": {
            "resume_used": resume_used,
            "fit_assessment": resume_alignment.get("fit_assessment", "").strip(),
            "matching_evidence": resume_alignment.get("matching_evidence") or [],
            "risk_gaps": resume_alignment.get("risk_gaps") or [],
            "recommended_stories": resume_alignment.get("recommended_stories") or [],
        },
        "prep_priorities": data.get("prep_priorities") or [],
        "question_blueprint": data.get("question_blueprint") or [],
        "jd_excerpt": jd_text.strip()[:1500],
    }
    return preview


def generate_job_prep_preview(
    jd_text: str,
    user_id: str,
    *,
    company: str | None = None,
    position: str | None = None,
    use_resume: bool = True,
) -> dict:
    """Analyze JD and candidate fit before starting the session."""
    resume_context, resume_used = _get_resume_context(user_id, use_resume)
    knowledge_ctx = _get_knowledge_for_jd(jd_text, user_id)
    prompt = JOB_PREP_PREVIEW_PROMPT.format(
        company=(company or "未提供").strip(),
        position=(position or "未提供").strip(),
        jd_text=jd_text.strip()[:6000],
        user_profile=get_profile_summary(user_id),
        resume_context=resume_context,
        knowledge_context=knowledge_ctx or "（暂无知识库数据）",
    )

    llm = get_langchain_llm()
    response = llm.invoke([
        SystemMessage(content="你是 JD 备面分析引擎。只返回 JSON。"),
        HumanMessage(content=prompt),
    ])

    raw = chunk_text(response)
    if not raw.strip():
        # Reasoning model returned thinking only, no visible JSON (billed but empty).
        logger.error("JD prep preview returned empty content (reasoning budget exhausted?)")
        raise RuntimeError("JD 分析失败：模型未返回正文（可能思考预算耗尽），请重试或降低该渠道的 reasoning_effort。")
    try:
        parsed = _parse_json_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError(f"Expected dict, got {type(parsed)}")
    except Exception as exc:
        logger.error(f"JD prep preview failed: {exc}")
        logger.error(f"LLM raw response: {raw[:800]}")
        raise RuntimeError("JD 分析失败，LLM 返回格式异常。请重试。")

    return _normalize_preview(
        parsed,
        company=company,
        position=position,
        jd_text=jd_text,
        resume_used=resume_used,
    )


def generate_job_prep_questions(
    jd_text: str,
    preview: dict,
    user_id: str,
    *,
    use_resume: bool = True,
) -> list[dict]:
    """Generate a structured JD-oriented mock interview."""
    resume_context, _ = _get_resume_context(user_id, use_resume)
    knowledge_ctx = _get_knowledge_for_jd(jd_text, user_id)
    prompt = JOB_PREP_QUESTION_GEN_PROMPT.format(
        preview_json=json.dumps(preview, ensure_ascii=False, indent=2)[:5000],
        company=preview.get("company") or "未提供",
        position=preview.get("position") or "未提供",
        jd_text=jd_text.strip()[:5000],
        user_profile=get_profile_summary(user_id),
        resume_context=resume_context,
        knowledge_context=knowledge_ctx or "（暂无知识库数据）",
    )

    llm = get_langchain_llm()
    response = llm.invoke([
        SystemMessage(content="你是 JD 备面出题引擎。只返回 JSON 数组。"),
        HumanMessage(content=prompt),
    ])

    raw = chunk_text(response)
    if not raw.strip():
        logger.error("JD prep question generation returned empty content (reasoning budget exhausted?)")
        raise RuntimeError("JD 备面出题失败：模型未返回正文（可能思考预算耗尽），请重试或降低该渠道的 reasoning_effort。")
    try:
        questions = _parse_json_response(raw)
        if not isinstance(questions, list):
            raise ValueError(f"Expected list, got {type(questions)}")
    except Exception as exc:
        # Truncated mid-array by max_tokens → salvage the complete question objects.
        salvaged, _ = extract_complete_objects(raw)
        if salvaged:
            logger.warning("JD prep question JSON parse failed (%s); salvaged %d objects", exc, len(salvaged))
            questions = salvaged
        else:
            logger.error(f"JD prep question generation failed: {exc}")
            logger.error(f"LLM raw response: {raw[:800]}")
            raise RuntimeError("JD 备面出题失败，LLM 返回格式异常。请重试。")

    normalized = []
    for i, q in enumerate(questions[:8], start=1):
        if not isinstance(q, dict):
            continue
        normalized.append({
            # Force integer id — don't trust LLM-provided ids (it emits strings
            # like "Q2", which break the frontend's per-question persistence).
            "id": i,
            "question": q.get("question", "").strip(),
            "difficulty": int(q.get("difficulty", 3) or 3),
            "focus_area": q.get("focus_area", "").strip(),
            "category": q.get("category", "").strip(),
            "intent": q.get("intent", "").strip(),
        })
    if len(normalized) < 4:
        raise RuntimeError("JD 备面出题失败，生成的问题数量不足。请重试。")
    return normalized


def evaluate_job_prep_answers(
    questions: list[dict],
    answers: list[dict],
    preview: dict,
    user_id: str,
) -> dict:
    """Evaluate answers against the JD's real hiring bar."""
    answer_map = {a["question_id"]: a["answer"] for a in answers}
    answered_questions = [q for q in questions if answer_map.get(q["id"])]

    qa_lines = []
    for q in answered_questions:
        qid = q["id"]
        qa_lines.append(
            f"### Q{qid} | {q.get('category', '未分类')} | 难度 {q.get('difficulty', 3)}/5\n"
            f"**考察点**: {q.get('focus_area', '')}\n"
            f"**题目**: {q['question']}\n"
            f"**回答**: {answer_map[qid]}"
        )

    jd_text = preview.get("jd_excerpt", "")
    knowledge_ctx = _get_knowledge_for_jd(jd_text, user_id) if jd_text else ""
    prompt = JOB_PREP_EVAL_PROMPT.format(
        company=preview.get("company") or "未提供",
        position=preview.get("position") or "未提供",
        preview_json=json.dumps(preview, ensure_ascii=False, indent=2)[:5000],
        qa_pairs="\n\n".join(qa_lines) or "候选人未作答",
        knowledge_context=knowledge_ctx or "（暂无知识库数据）",
    )

    llm = get_langchain_llm()
    response = llm.invoke([
        SystemMessage(content="你是 JD 备面评估引擎。只返回 JSON。"),
        HumanMessage(content=prompt),
    ])

    try:
        result = _parse_json_response(response.content)
        if not isinstance(result, dict):
            raise ValueError(f"Expected dict, got {type(result)}")
        return result
    except Exception as exc:
        logger.error(f"JD prep evaluation failed: {exc}")
        logger.error(f"LLM raw response: {response.content[:800]}")
        return {
            "scores": [
                {
                    "question_id": q["id"],
                    "score": None,
                    "assessment": "评估解析失败，请重试",
                    "improvement": "",
                    "understanding": "",
                    "weak_point": None,
                    "key_missing": [],
                    "role_expectation": "",
                }
                for q in questions
            ],
            "overall": {
                "avg_score": None,
                "summary": "评估结果解析失败，请重新提交。",
                "role_fit_summary": "",
                "interviewer_hotspots": [],
                "prep_priorities": [],
                "new_weak_points": [],
                "new_strong_points": [],
                "communication_observations": {},
                "thinking_patterns": {},
                "dimension_scores": {},
            },
        }


async def stream_generate_job_prep_preview(
    jd_text: str,
    user_id: str,
    *,
    company: str | None = None,
    position: str | None = None,
    use_resume: bool = True,
) -> AsyncGenerator[tuple[str, str | dict], None]:
    from backend.utils.sse_helpers import stream_llm_sse, sse_event

    resume_context, resume_used = _get_resume_context(user_id, use_resume)
    knowledge_ctx = _get_knowledge_for_jd(jd_text, user_id)
    prompt = JOB_PREP_PREVIEW_PROMPT.format(
        company=(company or "未提供").strip(),
        position=(position or "未提供").strip(),
        jd_text=jd_text.strip()[:6000],
        user_profile=get_profile_summary(user_id),
        resume_context=resume_context,
        knowledge_context=knowledge_ctx or "（暂无知识库数据）",
    )

    lc_messages = [
        SystemMessage(content="你是 JD 备面分析引擎。只返回 JSON。"),
        HumanMessage(content=prompt),
    ]

    raw = ""
    async for kind, value in stream_llm_sse(lc_messages, progress_prefix="正在分析 JD"):
        if kind == "sse":
            yield ("sse", value)
        else:
            raw = value

    try:
        parsed = _parse_json_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError(f"Expected dict, got {type(parsed)}")
    except Exception as exc:
        logger.error(f"JD prep preview failed: {exc}")
        yield ("sse", sse_event({"type": "error", "message": "JD 分析失败，LLM 返回格式异常。请重试。"}))
        return

    preview = _normalize_preview(
        parsed, company=company, position=position,
        jd_text=jd_text, resume_used=resume_used,
    )
    yield ("result", preview)


async def stream_generate_job_prep_questions(
    jd_text: str,
    preview: dict,
    user_id: str,
    *,
    use_resume: bool = True,
) -> AsyncGenerator[tuple[str, str | list], None]:
    from backend.utils.sse_helpers import stream_llm_sse, sse_event

    resume_context, _ = _get_resume_context(user_id, use_resume)
    knowledge_ctx = _get_knowledge_for_jd(jd_text, user_id)
    prompt = JOB_PREP_QUESTION_GEN_PROMPT.format(
        preview_json=json.dumps(preview, ensure_ascii=False, indent=2)[:5000],
        company=preview.get("company") or "未提供",
        position=preview.get("position") or "未提供",
        jd_text=jd_text.strip()[:5000],
        user_profile=get_profile_summary(user_id),
        resume_context=resume_context,
        knowledge_context=knowledge_ctx or "（暂无知识库数据）",
    )

    lc_messages = [
        SystemMessage(content="你是 JD 备面出题引擎。只返回 JSON 数组。"),
        HumanMessage(content=prompt),
    ]

    raw = ""
    async for kind, value in stream_llm_sse(lc_messages, progress_prefix="正在生成训练题目"):
        if kind == "sse":
            yield ("sse", value)
        else:
            raw = value

    try:
        questions = _parse_json_response(raw)
        if not isinstance(questions, list):
            raise ValueError(f"Expected list, got {type(questions)}")
    except Exception as exc:
        logger.error(f"JD prep question generation failed: {exc}")
        yield ("sse", sse_event({"type": "error", "message": "JD 备面出题失败，LLM 返回格式异常。请重试。"}))
        return

    normalized = []
    for i, q in enumerate(questions[:8], start=1):
        if not isinstance(q, dict):
            continue
        normalized.append({
            # Force integer id — don't trust LLM-provided ids (it emits strings
            # like "Q2", which break the frontend's per-question persistence).
            "id": i,
            "question": q.get("question", "").strip(),
            "difficulty": int(q.get("difficulty", 3) or 3),
            "focus_area": q.get("focus_area", "").strip(),
            "category": q.get("category", "").strip(),
            "intent": q.get("intent", "").strip(),
        })
    if len(normalized) < 4:
        yield ("sse", sse_event({"type": "error", "message": "JD 备面出题失败，生成的问题数量不足。请重试。"}))
        return
    yield ("result", normalized)


async def stream_evaluate_job_prep_answers(
    questions: list[dict], answers: list[dict], preview: dict, user_id: str,
) -> AsyncGenerator[str, None]:
    """Stream SSE events while evaluating JD prep answers."""
    answer_map = {a["question_id"]: a["answer"] for a in answers}
    answered_questions = [q for q in questions if answer_map.get(q["id"])]

    yield f"data: {json.dumps({'type': 'eval_start', 'total': len(answered_questions)}, ensure_ascii=False)}\n\n"

    qa_lines = []
    for q in answered_questions:
        qid = q["id"]
        qa_lines.append(
            f"### Q{qid} | {q.get('category', '未分类')} | 难度 {q.get('difficulty', 3)}/5\n"
            f"**考察点**: {q.get('focus_area', '')}\n"
            f"**题目**: {q['question']}\n"
            f"**回答**: {answer_map[qid]}"
        )

    jd_text = preview.get("jd_excerpt", "")
    knowledge_ctx = _get_knowledge_for_jd(jd_text, user_id) if jd_text else ""
    prompt = JOB_PREP_EVAL_PROMPT.format(
        company=preview.get("company") or "未提供",
        position=preview.get("position") or "未提供",
        preview_json=json.dumps(preview, ensure_ascii=False, indent=2)[:5000],
        qa_pairs="\n\n".join(qa_lines) or "候选人未作答",
        knowledge_context=knowledge_ctx or "（暂无知识库数据）",
    )

    llm = get_langchain_llm()
    lc_messages = [
        SystemMessage(content="你是 JD 备面评估引擎。只返回 JSON。"),
        HumanMessage(content=prompt),
    ]

    accumulated = ""
    chars_since_heartbeat = 0
    _fallback_scores = lambda: [{"question_id": q["id"], "score": None, "assessment": "评估失败"} for q in questions]
    _fallback_overall = lambda: {"avg_score": None, "summary": "评估过程出错。", "new_weak_points": [], "new_strong_points": [], "dimension_scores": {}}

    try:
        async for kind, delta in iter_llm_stream(llm, lc_messages):
            if kind == "idle":
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
            elif kind == "reasoning":
                yield f"data: {json.dumps({'type': 'eval_progress', 'message': '正在分析评估中...（思考中）'}, ensure_ascii=False)}\n\n"
            elif kind == "token":
                accumulated += delta
                chars_since_heartbeat += len(delta)
                if chars_since_heartbeat >= 200:
                    chars_since_heartbeat = 0
                    yield f"data: {json.dumps({'type': 'eval_progress', 'message': f'正在分析评估中... ({len(accumulated)} 字)'}, ensure_ascii=False)}\n\n"
    except Exception as e:
        logger.error("JD prep streaming evaluation failed: %s", e)
        yield f"data: {json.dumps({'type': 'eval_result', 'data': {'scores': _fallback_scores(), 'overall': _fallback_overall()}}, ensure_ascii=False)}\n\n"
        return

    try:
        result = _parse_json_response(accumulated)
        if not isinstance(result, dict):
            raise ValueError(f"Expected dict, got {type(result)}")
    except Exception as e:
        logger.error("JD prep evaluation parse failed: %s", e)
        result = {"scores": _fallback_scores(), "overall": _fallback_overall()}

    yield f"data: {json.dumps({'type': 'eval_result', 'data': result}, ensure_ascii=False)}\n\n"
