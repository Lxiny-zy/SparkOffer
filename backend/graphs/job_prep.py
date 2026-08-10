"""JD 定向备面服务."""
import hashlib
import json
import logging
import re
from collections.abc import AsyncGenerator

from langchain_core.messages import HumanMessage, SystemMessage

from backend.config import settings
from backend.graphs.topic_drill import _parse_json_response
from backend.indexer import retrieve_resume_chunks, load_topics
from backend.graphs.rag_retrieval import retrieve_for_job_prep
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
# Must equal the `jd_excerpt` truncation used when a preview/session is persisted
# (see _normalize_preview and routers/job_prep.py). The eval step only ever sees
# that excerpt, so keying the knowledge cache on this shared prefix is what makes
# preview / question-gen / eval hit the SAME cache entry.
_JD_QUERY_CHARS = 1500


def _match_topics_in_text(text: str, topics: dict) -> list[str]:
    """Topic keys whose name or key occurs in ``text``.

    Token-aware rather than a raw substring test: a bare ``in`` made "java" match
    inside "javascript", and could never match user-created topics whose key is a
    random hash (e.g. ``cb8f7fc8`` / "大厂题库"), so self-built knowledge bases
    were silently excluded. Returns [] on no match — callers decide whether a
    fallback is safe (it is for read-only retrieval, never for writeback).
    """
    lowered = text.lower()
    # Split on anything that isn't a word char or CJK so "Java/Spring" and
    # "Python、FastAPI" both tokenize; CJK names are matched by substring since
    # Chinese has no whitespace delimiters.
    tokens = set(re.split(r"[^0-9a-z一-鿿+#.]+", lowered)) - {""}

    matched = []
    for key, info in topics.items():
        name = str(info.get("name", key)).strip().lower()
        aliases = {a for a in (name, key.lower()) if a}
        for alias in aliases:
            hit = (
                alias in lowered if re.search(r"[一-鿿]", alias)  # CJK: substring
                else alias in tokens                              # ASCII: whole token
            )
            if hit:
                matched.append(key)
                break
    return matched


def _match_jd_topics(jd_text: str, user_id: str) -> list[str]:
    """Knowledge topics to retrieve from for a JD. Falls back to the first few
    topics on a miss so retrieval still has material to work with — safe here
    because this context is read-only (writeback uses a stricter matcher)."""
    topics = load_topics(user_id)
    if not topics:
        return []
    return _match_topics_in_text(jd_text, topics) or list(topics.keys())[:3]


def _jd_cache_key(jd_text: str, user_id: str) -> str:
    """Cache key for a prep flow's knowledge context.

    Digests the SAME prefix the eval step can supply. preview / question-gen see
    the full JD while the eval step only has ``preview["jd_excerpt"]`` (truncated
    to 1500 chars when the session was created), so digesting the raw text made
    the eval step miss the cache every single time and re-run the whole
    multi-topic retrieval. Keying on the shared prefix makes all three steps agree.
    """
    prefix = jd_text.strip()[:_JD_QUERY_CHARS]
    digest = hashlib.sha256(prefix.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"jdknow:{user_id}:{digest}"


def _save_jd_rag_metrics(
    session_id: str, user_id: str, topics: list[str],
    metrics: dict, chunk_count: int | None,
) -> None:
    """Persist JD retrieval health so the RAG dashboard covers this path too.

    Mirrors the drill's question_gen write (drill_pipeline). The topic column
    holds the joined matched topics — a JD session has no single one.
    """
    try:
        from backend.storage.rag_metrics_store import save_rag_metrics
        save_rag_metrics(
            session_id, user_id, ",".join(topics) or "jd_prep", "question_gen",
            relevance=metrics.get("relevance"),
            coverage=metrics.get("coverage"),
            diversity=metrics.get("diversity"),
            chunk_count=chunk_count,
            detail={"chunk_details": metrics.get("chunk_details", [])},
        )
    except Exception as exc:
        logger.warning("Failed to persist JD RAG metrics: %s", exc)


async def _get_knowledge_for_jd(
    jd_text: str, user_id: str, *, session_id: str | None = None,
) -> str:
    """Retrieve knowledge base context for a JD via the shared RAG pipeline.

    Cached by (user, jd-prefix) for the duration of a prep flow: the preview,
    question and eval steps all call this for the same JD, so without the cache
    the multi-topic retrieval would run 2–3× per session.

    Retrieval itself now goes through ``retrieve_for_job_prep`` — the same
    fan-out → RRF fusion → semantic dedup → rerank path the topic drill uses.
    The previous version issued one fixed query per topic and concatenated the
    raw results, with no fusion, no dedup and no reranking.

    ``session_id`` (question-gen only) persists the retrieval-health metrics so
    the JD path shows up on the RAG dashboard alongside topic drills.
    """
    try:
        matched = _match_jd_topics(jd_text, user_id)
        if not matched:
            return ""

        cache = get_cache()
        ck = _jd_cache_key(jd_text, user_id)
        cached = cache.get_json(ck)
        if cached is not None:
            # Entries hold {"context", "metrics"}; a bare string is a pre-upgrade
            # entry still inside its 10-minute TTL.
            if isinstance(cached, str):
                return cached
            context = cached.get("context", "")
            # The preview step warms this cache, so question-gen (the step that
            # carries session_id) normally short-circuits here — persist the
            # retrieval metrics from the cached run so the JD path still lands
            # on the RAG dashboard.
            if session_id and cached.get("metrics"):
                _save_jd_rag_metrics(
                    session_id, user_id, cached.get("topics") or matched[:5],
                    cached["metrics"], cached.get("chunk_count"),
                )
            return context

        # Query with the JD itself. The old code passed a fixed
        # "核心知识点 面试常见问题" string for every topic, so the JD only chose
        # WHICH index to hit and never influenced WHAT was retrieved — every JD
        # got the same generic chunks back.
        query = jd_text.strip()[:_JD_QUERY_CHARS]
        chunks, stats = await retrieve_for_job_prep(
            matched[:5], user_id, [query], query, per_query_top_k=3, final_top_n=12,
        )
        logger.info(
            "JD RAG: topics=%s fanout=%d raw=%d fused=%d final=%d reranker=%s",
            matched[:5], stats.queries, stats.raw_chunks, stats.fused_chunks,
            stats.final_chunks, stats.reranker_status,
        )
        if session_id and stats.rag_metrics:
            _save_jd_rag_metrics(
                session_id, user_id, matched[:5], stats.rag_metrics, stats.final_chunks,
            )
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
        cache.set_json(ck, {
            "context": result,
            "metrics": stats.rag_metrics,
            "chunk_count": stats.final_chunks,
            "topics": matched[:5],
        }, _JD_KNOWLEDGE_TTL)
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
        resume_context = retrieve_resume_chunks(
            "候选人的项目经历、技术栈、AI/后端/工程化相关实践，以及最适合拿来面这个岗位的经历",
            user_id,
            top_k=6,
        )
    except Exception as exc:
        logger.warning("Failed to load resume context for JD prep: %s", exc)
        return "简历检索失败，本次按无简历联动处理", False

    # An empty retrieval means the resume produced no usable text (scan-only PDF,
    # index not built, embedding failure). Reporting resume_used=True here is what
    # made the UI claim "简历联动已启用" while the prompt carried nothing, so the
    # model invented experience. Fail honestly instead.
    if not resume_context:
        logger.warning("Resume index returned no chunks for user=%s", user_id)
        return "简历内容为空或未能解析（可能是扫描件 PDF），本次按无简历联动处理", False

    # Token-budget the resume context (~30% of the input window) instead of [:5000].
    from backend.context_assembler import ContextBudget, Section, resolve_input_budget
    fit = ContextBudget(max(1000, int(resolve_input_budget() * 0.3))).pack(
        [Section("resume", "\n\n---\n\n".join(resume_context), priority=1, min_tokens=200)]
    ).get("resume")
    return fit, True


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
    knowledge_ctx = await _get_knowledge_for_jd(jd_text, user_id)
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
        elif kind == "error":
            return  # error SSE already forwarded
        else:
            raw = value

    if not raw.strip():
        # Reasoning model returned thinking only, no visible JSON (billed but empty).
        logger.error("JD prep preview returned empty content (reasoning budget exhausted?)")
        yield ("sse", sse_event({
            "type": "error",
            "message": "JD 分析失败：模型未返回正文（可能思考预算耗尽），请重试或降低该渠道的 reasoning_effort。",
        }))
        return

    try:
        parsed = _parse_json_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError(f"Expected dict, got {type(parsed)}")
    except Exception as exc:
        logger.error(f"JD prep preview failed: {exc}")
        logger.error(f"LLM raw response: {raw[:800]}")
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
    session_id: str | None = None,
) -> AsyncGenerator[tuple[str, str | list], None]:
    from backend.utils.sse_helpers import stream_llm_sse, sse_event

    resume_context, _ = _get_resume_context(user_id, use_resume)
    knowledge_ctx = await _get_knowledge_for_jd(jd_text, user_id, session_id=session_id)
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
        elif kind == "error":
            return  # error SSE already forwarded
        else:
            raw = value

    if not raw.strip():
        logger.error("JD prep question generation returned empty content (reasoning budget exhausted?)")
        yield ("sse", sse_event({
            "type": "error",
            "message": "JD 备面出题失败：模型未返回正文（可能思考预算耗尽），请重试或降低该渠道的 reasoning_effort。",
        }))
        return

    try:
        questions = _parse_json_response(raw)
        if not isinstance(questions, list):
            raise ValueError(f"Expected list, got {type(questions)}")
    except Exception as exc:
        # Truncated mid-array by max_tokens → salvage the complete question
        # objects rather than discarding a nearly-complete generation.
        salvaged, _ = extract_complete_objects(raw)
        if salvaged:
            logger.warning(
                "JD prep question JSON parse failed (%s); salvaged %d objects",
                exc, len(salvaged),
            )
            questions = salvaged
        else:
            logger.error(f"JD prep question generation failed: {exc}")
            logger.error(f"LLM raw response: {raw[:800]}")
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
    answer_map = {a["question_id"]: a.get("answer", "") for a in answers if a.get("question_id")}
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
    knowledge_ctx = await _get_knowledge_for_jd(jd_text, user_id) if jd_text else ""
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

    def _fallback_scores():
        return [
            {"question_id": q["id"], "score": None, "assessment": "评估失败"}
            for q in questions
        ]

    def _fallback_overall():
        return {
            "avg_score": None,
            "summary": "评估过程出错。",
            "new_weak_points": [],
            "new_strong_points": [],
            "dimension_scores": {},
        }

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
