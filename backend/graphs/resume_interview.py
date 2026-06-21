"""模式1: 简历模拟面试 LangGraph."""
import json
import logging
import re

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END

from backend.models import ResumeInterviewState, InterviewPhase
from backend.config import settings
from backend.llm_provider import get_langchain_llm
from backend.graphs.checkpointer import get_checkpointer
from backend.indexer import query_resume, gather_topic_contexts, load_topics
from backend.memory import get_profile_summary
from backend.context_assembler import resolve_input_budget, count_tokens, ContextBudget, Section
from backend.prompts.interviewer import RESUME_INTERVIEWER_SYSTEM, RESUME_TURN_CONTEXT

logger = logging.getLogger("uvicorn")


def _retrieve_all_topic_knowledge(user_id: str, query: str = "") -> str:
    """Retrieve knowledge snippets across all user topics for resume interview.

    Topics are retrieved concurrently (one shared pool) under a single overall
    deadline — previously each topic ran in its own max_workers=1 pool, serially,
    for up to 6×60s.
    """
    try:
        topics = load_topics(user_id)
        if not topics:
            return ""
        q = query or "核心知识点 面试常见问题"
        topic_keys = list(topics.keys())[:6]
        per_topic = gather_topic_contexts([(k, q) for k in topic_keys], user_id, top_k=2)
        chunks = [c for topic_chunks in per_topic for c in topic_chunks]
        if not chunks:
            return ""
        # Token-budget knowledge to a share of the input window (leave room for the
        # resume + profile prefix and the live conversation history) instead of the
        # old 3000-char total / 500-char-per-chunk hard cuts.
        kb_budget = max(1000, int(resolve_input_budget() * 0.4))
        return ContextBudget(kb_budget).pack(
            [Section("kb", "\n\n---\n\n".join(chunks), priority=1, min_tokens=200)]
        ).get("kb")
    except Exception as e:
        logger.warning("Knowledge retrieval for resume interview failed: %s", e)
        return ""


PHASE_ORDER = [
    InterviewPhase.GREETING.value,
    InterviewPhase.SELF_INTRO.value,
    InterviewPhase.TECHNICAL.value,
    InterviewPhase.PROJECT_DEEP_DIVE.value,
    InterviewPhase.REVERSE_QA.value,
]

# Hard ceiling per phase to prevent infinite loops
HARD_MAX_PER_PHASE = 10

_EVAL_PATTERN = re.compile(r"<!--EVAL:(.*?)-->", re.DOTALL)


def _parse_inline_eval(content: str) -> tuple[str, dict | None]:
    """Extract and strip hidden eval JSON from interviewer response.

    Uses the LAST EVAL marker (the real one is the response's final line; an
    earlier match would be a stray example the model echoed mid-answer). The
    parsed ``score`` is clamped to 0-10 and dropped if non-numeric / out of range,
    so a malformed or injected marker can't poison the profile.

    Returns (clean_content, eval_dict_or_None).
    """
    matches = list(_EVAL_PATTERN.finditer(content))
    if not matches:
        return content, None

    m = matches[-1]
    clean = _EVAL_PATTERN.sub("", content).rstrip()
    try:
        eval_data = json.loads(m.group(1))
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse inline eval: {m.group(1)[:100]}")
        return clean, None

    if not isinstance(eval_data, dict):
        return clean, None

    # Clamp / validate score: keep only numeric 0-10, else drop the field so
    # downstream averaging ignores it rather than ingesting a bogus value.
    score = eval_data.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        eval_data.pop("score", None)
    else:
        eval_data["score"] = max(0, min(10, int(round(score))))

    return clean, eval_data


def _make_init_interview(user_id: str):
    """Create init_interview node bound to a specific user."""
    def init_interview(state: ResumeInterviewState) -> dict:
        """Load resume context, freeze the stable system prefix, prepare the opening."""
        resume_ctx = query_resume("列出候选人的所有项目经历、技能和教育背景", user_id)
        knowledge_ctx = _retrieve_all_topic_knowledge(user_id)

        # Build the STABLE system prefix ONCE. It holds only data fixed for the
        # whole interview (resume / knowledge / long-term profile); the per-turn
        # phase + asked-questions ride in RESUME_TURN_CONTEXT instead, so this
        # prefix stays byte-identical across turns → prompt-prefix cache hits.
        system_prompt = RESUME_INTERVIEWER_SYSTEM.format(
            resume_context=resume_ctx,
            knowledge_context=knowledge_ctx or "（暂无知识库数据）",
            user_profile=get_profile_summary(user_id),
        )

        llm = get_langchain_llm()
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content="面试开始，请开场并让候选人做自我介绍。"),
        ])

        return {
            "messages": [response],
            "system_prompt": system_prompt,
            "resume_context": resume_ctx,
            "knowledge_context": knowledge_ctx,
            "phase": InterviewPhase.GREETING.value,
            "questions_asked": [],
            "phase_question_count": 0,
            "is_finished": False,
            "eval_history": [],
        }
    return init_interview


def _make_interviewer_ask(user_id: str):
    """Create interviewer_ask node bound to a specific user."""
    def interviewer_ask(state: ResumeInterviewState) -> dict:
        """Generate next question based on current phase and conversation."""
        asked = state.get("questions_asked", [])
        asked_str = "\n".join(f"- {q}" for q in asked) if asked else "无"

        # STABLE system prefix was frozen at init (resume / knowledge / profile).
        # Rebuild only as a fallback for sessions checkpointed before this field
        # existed.
        stable = state.get("system_prompt")
        if not stable:
            stable = RESUME_INTERVIEWER_SYSTEM.format(
                resume_context=state.get("resume_context", ""),
                knowledge_context=state.get("knowledge_context", "") or "（暂无知识库数据）",
                user_profile=get_profile_summary(user_id),
            )

        # Per-turn volatile context (phase + asked) rides in a trailing message so
        # `stable` never changes across turns.
        turn_ctx = RESUME_TURN_CONTEXT.format(
            phase=state.get("phase", "technical"),
            asked_questions=asked_str,
        )

        # Budget the history: keep the newest turns that fit after reserving room
        # for the stable prefix + turn context. Full history stays in state; we
        # only trim what we send the model this turn (always keep the latest turn).
        budget = resolve_input_budget()
        hist_budget = max(2000, budget - count_tokens(stable) - count_tokens(turn_ctx))
        history = list(state.get("messages", []))
        kept: list = []
        used = 0
        for m in reversed(history):
            t = count_tokens(getattr(m, "content", "") or "") + 4
            if kept and used + t > hist_budget:
                break
            kept.append(m)
            used += t
        kept.reverse()

        llm = get_langchain_llm()
        messages = [SystemMessage(content=stable)] + kept + [SystemMessage(content=turn_ctx)]
        response = llm.invoke(messages)

        # Parse and strip inline eval from response
        clean_content, eval_data = _parse_inline_eval(response.content)
        count = state.get("phase_question_count", 0)

        result = {
            "messages": [AIMessage(content=clean_content)],
            "questions_asked": asked + [clean_content[:100]],
            "phase_question_count": count + 1,
        }

        if eval_data:
            eval_data["phase"] = state.get("phase", "")
            eval_data["question_index"] = count
            result["last_eval"] = eval_data
            result["eval_history"] = list(state.get("eval_history", [])) + [eval_data]
            logger.info(
                f"Inline eval: phase={eval_data['phase']}, "
                f"score={eval_data.get('score')}, "
                f"should_advance={eval_data.get('should_advance')}"
            )

        return result
    return interviewer_ask


def route_after_answer(state: ResumeInterviewState) -> str:
    """After user answers: keep asking, advance phase, or end."""
    if state.get("is_finished"):
        return "end"

    phase = state.get("phase", "greeting")
    count = state.get("phase_question_count", 0)
    last_eval = state.get("last_eval")

    # Hard ceiling — prevent infinite loops regardless of eval data
    if count >= HARD_MAX_PER_PHASE:
        return "advance"

    # Simple phases: count-based rules
    if phase == "greeting" and count >= 1:
        return "advance"
    if phase == "self_intro" and count >= 2:
        return "advance"
    if phase == "reverse_qa" and count >= 2:
        return "end"

    # Technical / project_deep_dive: eval-driven with count fallback
    if phase in ("technical", "project_deep_dive"):
        # Need at least 2 questions before considering advancement
        if count >= 2 and last_eval and last_eval.get("should_advance"):
            logger.info(f"Eval-driven advance: {phase} after {count} questions")
            return "advance"

        # Count-based fallback
        max_q = settings.max_questions_per_phase
        if count >= max_q:
            return "advance"

    return "ask"


def advance_phase(state: ResumeInterviewState) -> dict:
    """Move to the next interview phase."""
    current_phase = state.get("phase", "greeting")

    try:
        idx = PHASE_ORDER.index(current_phase)
    except ValueError:
        return {"is_finished": True}

    if idx >= len(PHASE_ORDER) - 1:
        return {"is_finished": True}

    return {
        "phase": PHASE_ORDER[idx + 1],
        "phase_question_count": 0,
        "last_eval": {},
    }


def wait_for_answer(state: ResumeInterviewState) -> dict:
    """Graph pauses here for user input."""
    return {}


def compile_resume_interview(user_id: str):
    """Build and compile the resume interview graph."""
    graph = StateGraph(ResumeInterviewState)

    graph.add_node("init", _make_init_interview(user_id))
    graph.add_node("ask", _make_interviewer_ask(user_id))
    graph.add_node("advance", advance_phase)
    graph.add_node("wait", wait_for_answer)

    graph.add_edge(START, "init")
    graph.add_edge("init", "wait")
    graph.add_edge("ask", "wait")
    graph.add_edge("advance", "ask")

    graph.add_conditional_edges("wait", route_after_answer, {
        "ask": "ask",
        "advance": "advance",
        "end": END,
    })

    return graph.compile(
        checkpointer=get_checkpointer(),
        interrupt_before=["wait"],
    )
