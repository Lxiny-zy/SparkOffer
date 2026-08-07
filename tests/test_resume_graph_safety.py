import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.graphs import resume_interview


class _StaticLLM:
    def __init__(self, content):
        self.content = content

    def invoke(self, _messages):
        return AIMessage(content=self.content)


def test_resume_opening_rejects_empty_model_content(monkeypatch):
    monkeypatch.setattr(resume_interview, "query_resume", lambda *args: "resume")
    monkeypatch.setattr(
        resume_interview, "_retrieve_all_topic_knowledge", lambda *args: "knowledge",
    )
    monkeypatch.setattr(resume_interview, "get_profile_summary", lambda *args: "profile")
    monkeypatch.setattr(
        resume_interview, "get_langchain_llm", lambda: _StaticLLM(""),
    )

    with pytest.raises(RuntimeError, match="empty opening"):
        resume_interview._make_init_interview("u1")({})


def test_resume_turn_rejects_empty_model_content(monkeypatch):
    monkeypatch.setattr(
        resume_interview, "get_langchain_llm", lambda: _StaticLLM([]),
    )
    state = {
        "system_prompt": "stable",
        "messages": [HumanMessage(content="answer")],
        "phase": "technical",
        "questions_asked": [],
        "phase_question_count": 0,
    }

    with pytest.raises(RuntimeError, match="empty reply"):
        resume_interview._make_interviewer_ask("u1")(state)


def test_resume_turn_rejects_eval_only_model_content(monkeypatch):
    monkeypatch.setattr(
        resume_interview,
        "get_langchain_llm",
        lambda: _StaticLLM('<!--EVAL:{"score": 8}-->'),
    )
    state = {
        "system_prompt": "stable",
        "messages": [HumanMessage(content="answer")],
        "phase": "technical",
        "questions_asked": [],
        "phase_question_count": 0,
    }

    with pytest.raises(RuntimeError, match="empty reply"):
        resume_interview._make_interviewer_ask("u1")(state)


def test_second_reverse_question_is_answered_before_session_finishes(monkeypatch):
    monkeypatch.setattr(
        resume_interview, "get_langchain_llm", lambda: _StaticLLM("这是对第二个反问的回答。"),
    )
    state = {
        "system_prompt": "stable",
        "messages": [HumanMessage(content="团队如何做模型评测？")],
        "phase": "reverse_qa",
        "questions_asked": ["invite", "first answer"],
        "phase_question_count": 2,
        "eval_history": [],
    }

    assert resume_interview.route_after_answer(state) == "ask"

    result = resume_interview._make_interviewer_ask("u1")(state)

    assert result["messages"][0].content == "这是对第二个反问的回答。"
    assert result["phase"] == "end"
    assert result["is_finished"] is True
    assert result["phase_question_count"] == 3
