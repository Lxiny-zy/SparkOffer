"""模式2: 专项强化训练 — 批量出题 + 批量评估（不再使用 LangGraph）."""
import asyncio
import concurrent.futures
import json
import logging
from collections.abc import AsyncGenerator

from langchain_core.messages import SystemMessage, HumanMessage

from backend.config import settings
from backend.llm_provider import get_langchain_llm
from backend.indexer import retrieve_topic_context, gather_topic_contexts, safe_retrieve_topic_context, load_topics
from backend.memory import get_profile_summary, get_profile_summary_for_drill, get_topic_context_for_drill
from backend.prompts.interviewer import DRILL_QUESTION_GEN_PROMPT, DRILL_BATCH_EVAL_PROMPT
from backend.utils.sse_helpers import iter_llm_stream, chunk_text
from backend.utils.stream_parser import extract_complete_objects

_logger = logging.getLogger("uvicorn")

# Timeout for knowledge retrieval operations (seconds)
_RETRIEVAL_TIMEOUT = 60.0


def _safe_retrieve(topic: str, question: str, user_id: str, top_k: int = 5) -> list[str]:
    """Synchronous retrieve with timeout protection."""
    # No `with` block: ThreadPoolExecutor.__exit__ does shutdown(wait=True),
    # which would block on the hung retrieval thread until it finishes — making
    # the 60s timeout purely cosmetic. shutdown(wait=False) returns at the
    # deadline and abandons the worker thread (it dies when retrieval returns).
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(retrieve_topic_context, topic, question, user_id, top_k)
        return future.result(timeout=_RETRIEVAL_TIMEOUT)
    except concurrent.futures.TimeoutError:
        _logger.warning(f"Knowledge retrieval timed out ({_RETRIEVAL_TIMEOUT}s) for topic={topic}")
        return []
    except Exception as e:
        _logger.warning(f"Knowledge retrieval failed for topic={topic}: {e}")
        return []
    finally:
        executor.shutdown(wait=False)


def _get_topic_display(user_id: str) -> dict[str, str]:
    """Dynamic {key: display_name} from topics.json."""
    return {k: v["name"] for k, v in load_topics(user_id).items()}


def _parse_json_response(content: str) -> dict | list:
    """Extract JSON from LLM response, handling various formats."""
    import re
    content = content.strip()

    # Try direct parse first
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Extract from markdown code block
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", content)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Find first [ or { and parse from there
    for i, c in enumerate(content):
        if c in ("[", "{"):
            try:
                return json.loads(content[i:])
            except json.JSONDecodeError:
                pass
            break

    raise json.JSONDecodeError("No valid JSON found", content, 0)


def _load_high_freq(topic: str, user_id: str) -> str:
    """Load high-frequency question bank for a topic."""
    filepath = settings.user_high_freq_path(user_id) / f"{topic}.md"
    if filepath.exists():
        return filepath.read_text(encoding="utf-8").strip()
    return ""


def generate_drill_questions(topic: str, user_id: str) -> list[dict]:
    """Generate 10 personalized questions for a topic. 1 LLM call."""
    from backend.spaced_repetition import get_due_reviews, init_sr_for_existing_points

    # Ensure existing weak points have SR state
    init_sr_for_existing_points(user_id)

    topic_display = _get_topic_display(user_id)
    topic_name = topic_display.get(topic, topic)
    drill_ctx = get_topic_context_for_drill(topic, user_id)

    # Spaced repetition: prioritize due reviews
    due_reviews = get_due_reviews(user_id, topic)
    due_points = [wp["point"] for wp in due_reviews[:5]]

    all_weak = list(drill_ctx["weak_points"])
    for dp in due_points:
        if dp not in all_weak:
            all_weak.insert(0, dp)

    # Retrieve knowledge — prioritize weak areas
    queries = []
    if all_weak:
        queries.append(" ".join(all_weak[:5]))
    queries.append(f"{topic_name} 核心知识点 面试常见问题")

    all_chunks = []
    for q in queries:
        all_chunks.extend(_safe_retrieve(topic, q, user_id, top_k=5))
    # Deduplicate and limit
    seen = set()
    unique_chunks = []
    for c in all_chunks:
        key = c[:100]
        if key not in seen:
            seen.add(key)
            unique_chunks.append(c)
    knowledge_ctx = "\n\n---\n\n".join(unique_chunks)[:5000]

    # Format past insights from vector retrieval
    past_insights_text = "\n".join(
        f"- {ins[:200]}" for ins in drill_ctx.get("past_insights", [])
    ) or "暂无历史数据"

    # Load high-frequency questions
    high_freq = _load_high_freq(topic, user_id) or "暂无"

    # Format weak points, marking due reviews
    weak_lines = []
    for w in all_weak[:10]:
        prefix = "[到期复习] " if w in due_points else ""
        weak_lines.append(f"- {prefix}{w}")

    # Difficulty range and question strategy based on mastery
    mastery_score = drill_ctx["mastery_score"]
    if mastery_score <= 30:
        diff_min, diff_max = 1, 3
        question_strategy = (
            "当前为新手阶段（掌握度 0-30），题目策略：\n"
            "- 70% 基础概念题 + 对比辨析题，30% 简单应用题\n"
            "- 基础概念题考的是「是什么」和「为什么」：核心定义、基本原理、常见术语的含义\n"
            "- 不要考底层实现细节、内核机制、源码级原理等深度概念\n"
            "- 不要出复杂场景设计题或系统架构题，先确认基础概念是否扎实\n"
            "- 概念题要考理解而非背诵——问「为什么这样设计」而非「请背诵定义」"
        )
    elif mastery_score <= 60:
        diff_min, diff_max = 2, 4
        question_strategy = (
            "当前有基础（掌握度 30-60），题目策略：\n"
            "- 40% 深度概念题（底层原理、实现机制、边界行为），40% 场景应用题，20% 设计权衡题\n"
            "- 可以考底层原理和内部机制，但场景题控制在单组件/单服务范围，不需要大规模系统设计"
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

    llm = get_langchain_llm()
    response = llm.invoke([
        SystemMessage(content="你是专项训练出题引擎。只返回 JSON 数组，不要其他内容。"),
        HumanMessage(content=prompt),
    ])

    raw = chunk_text(response)
    if not raw.strip():
        # A reasoning model can burn its whole token budget on thinking and return no
        # visible JSON (billed but empty). Surface a retryable message instead of letting
        # an empty string fall into _parse_json_response → JSONDecodeError → 500.
        _logger.error("Drill question generation returned empty content (reasoning budget exhausted?)")
        raise RuntimeError("出题失败：模型未返回题目正文（可能思考预算耗尽），请重试或降低该渠道的 reasoning_effort。")

    try:
        questions = _parse_json_response(raw)
        if not isinstance(questions, list):
            raise ValueError(f"Expected a list, got {type(questions)}")
    except (json.JSONDecodeError, ValueError) as e:
        # Output may have been truncated mid-array by max_tokens — salvage the complete
        # question objects that did stream rather than failing the whole request.
        salvaged, _ = extract_complete_objects(raw)
        if salvaged:
            _logger.warning("Drill question JSON parse failed (%s); salvaged %d complete objects", e, len(salvaged))
            questions = salvaged
        else:
            _logger.error(f"Drill question generation failed: {e}")
            _logger.error(f"LLM raw response: {raw[:500]}")
            raise RuntimeError(f"出题失败，LLM 返回格式异常: {e}")

    # Force contiguous integer ids — don't trust LLM-provided ids (it emits
    # strings like "Q2", which break the frontend's per-question persistence).
    for i, q in enumerate(questions):
        if isinstance(q, dict):
            q["id"] = i + 1
    return questions[:10]


def evaluate_drill_answers(topic: str, questions: list[dict], answers: list[dict],
                           user_id: str) -> dict:
    """Batch evaluate all answers. 1 LLM call."""
    topic_display = _get_topic_display(user_id)
    topic_name = topic_display.get(topic, topic)
    answer_map = {a["question_id"]: a["answer"] for a in answers}

    # Only evaluate answered questions
    answered_questions = [q for q in questions if answer_map.get(q["id"])]

    # Retrieve references for all answered questions concurrently (was: serial,
    # a fresh max_workers=1 pool per question).
    per_q_refs = gather_topic_contexts(
        [(topic, q["question"]) for q in answered_questions], user_id, top_k=2,
    )
    qa_lines = []
    ref_lines = []
    for q, refs in zip(answered_questions, per_q_refs):
        qid = q["id"]
        answer = answer_map[qid]
        qa_lines.append(f"### Q{qid} (难度 {q.get('difficulty', '?')}/5)\n**题目**: {q['question']}\n**回答**: {answer}")
        if refs:
            ref_lines.append(f"### Q{qid} 参考\n" + "\n".join(refs)[:800])

    prompt = DRILL_BATCH_EVAL_PROMPT.format(
        topic_name=topic_name,
        topic_key=topic,
        qa_pairs="\n\n".join(qa_lines),
        references="\n\n".join(ref_lines)[:4000],
    )

    llm = get_langchain_llm()
    response = llm.invoke([
        SystemMessage(content="你是训练评估引擎。只返回 JSON，不要其他内容。"),
        HumanMessage(content=prompt),
    ])
    # chunk_text, not response.content: content-block-list gateways would make
    # .strip()/slicing below throw outside the JSON-failure fallback.
    content = chunk_text(response)

    try:
        result = _parse_json_response(content)
        if not isinstance(result, dict):
            raise ValueError(f"Expected a dict, got {type(result)}")
        return result
    except (json.JSONDecodeError, ValueError, IndexError) as e:
        import logging
        logger = logging.getLogger("uvicorn")
        logger.error(f"Drill evaluation failed: {e}")
        logger.error(f"LLM raw response: {content[:500]}")
        # Evaluation fallback is acceptable — better than crashing
        return {
            "scores": [{"question_id": q["id"], "score": None, "assessment": "评估解析失败，请重试"} for q in questions],
            "overall": {"avg_score": None, "summary": "评估结果解析失败，请重新提交。", "new_weak_points": [], "new_strong_points": []},
        }


async def stream_evaluate_drill_answers(
    topic: str, questions: list[dict], answers: list[dict], user_id: str,
) -> AsyncGenerator[str, None]:
    """Stream SSE events while evaluating drill answers. Yields SSE lines."""
    topic_display = _get_topic_display(user_id)
    topic_name = topic_display.get(topic, topic)
    answer_map = {a["question_id"]: a["answer"] for a in answers}
    answered_questions = [q for q in questions if answer_map.get(q["id"])]

    yield f"data: {json.dumps({'type': 'eval_start', 'total': len(answered_questions)}, ensure_ascii=False)}\n\n"

    # Retrieve references for all answered questions concurrently on the loop
    # (was: serial _safe_retrieve, each spinning up its own thread pool).
    # Bounded at 2 like rag_retrieval._EMBED_CONCURRENCY: an unbounded gather
    # of up to 10 retrievals trips the embedding API's per-key concurrency
    # throttle, stalling every request to its full timeout and degrading ALL
    # references to [] — the evaluation then silently runs without any.
    if answered_questions:
        _eval_retrieval_sem = asyncio.Semaphore(2)

        async def _bounded_retrieve(q: dict) -> list[str]:
            async with _eval_retrieval_sem:
                return await safe_retrieve_topic_context(topic, q["question"], user_id, top_k=2)

        per_q_refs = await asyncio.gather(*[_bounded_retrieve(q) for q in answered_questions])
    else:
        per_q_refs = []

    qa_lines = []
    ref_lines = []
    for q, refs in zip(answered_questions, per_q_refs):
        qid = q["id"]
        answer = answer_map[qid]
        qa_lines.append(f"### Q{qid} (难度 {q.get('difficulty', '?')}/5)\n**题目**: {q['question']}\n**回答**: {answer}")
        if refs:
            ref_lines.append(f"### Q{qid} 参考\n" + "\n".join(refs)[:800])

    prompt = DRILL_BATCH_EVAL_PROMPT.format(
        topic_name=topic_name,
        topic_key=topic,
        qa_pairs="\n\n".join(qa_lines),
        references="\n\n".join(ref_lines)[:4000],
    )

    llm = get_langchain_llm()
    lc_messages = [
        SystemMessage(content="你是训练评估引擎。只返回 JSON，不要其他内容。"),
        HumanMessage(content=prompt),
    ]

    accumulated = ""
    chars_since_heartbeat = 0
    try:
        async for kind, delta in iter_llm_stream(llm, lc_messages):
            if kind == "idle":
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
            elif kind == "reasoning":
                # Keep the stream alive during the model's thinking phase.
                yield f"data: {json.dumps({'type': 'eval_progress', 'message': '正在分析评估中...（思考中）'}, ensure_ascii=False)}\n\n"
            elif kind == "token":
                accumulated += delta
                chars_since_heartbeat += len(delta)
                if chars_since_heartbeat >= 200:
                    chars_since_heartbeat = 0
                    yield f"data: {json.dumps({'type': 'eval_progress', 'message': f'正在分析评估中... ({len(accumulated)} 字)'}, ensure_ascii=False)}\n\n"
    except Exception as e:
        _logger.error("Drill streaming evaluation failed: %s", e)
        yield f"data: {json.dumps({'type': 'eval_result', 'data': {'scores': [{'question_id': q['id'], 'score': None, 'assessment': '评估失败'} for q in questions], 'overall': {'avg_score': None, 'summary': '评估过程出错，请重试。', 'new_weak_points': [], 'new_strong_points': []}}}, ensure_ascii=False)}\n\n"
        return

    try:
        result = _parse_json_response(accumulated)
        if not isinstance(result, dict):
            raise ValueError(f"Expected a dict, got {type(result)}")
    except (json.JSONDecodeError, ValueError) as e:
        _logger.error("Drill evaluation parse failed: %s", e)
        _logger.error("LLM raw response: %s", accumulated[:500])
        result = {
            "scores": [{"question_id": q["id"], "score": None, "assessment": "评估解析失败，请重试"} for q in questions],
            "overall": {"avg_score": None, "summary": "评估结果解析失败。", "new_weak_points": [], "new_strong_points": []},
        }

    yield f"data: {json.dumps({'type': 'eval_result', 'data': result}, ensure_ascii=False)}\n\n"
