from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph

import backend.storage.database as database
from backend.models import (
    ChatRequest,
    ResumeInterviewState,
    RetryInterviewReplyRequest,
)
from backend.routers import interview
from backend.storage.sessions import (
    abort_session_sync_claim,
    append_message,
    commit_resume_turn,
    create_session,
    get_session,
    mark_resume_session_initialized,
    mark_session_sync_step,
    replace_resume_reply,
    release_resume_turn_claim,
    release_session_evaluation_claim,
    release_session_sync_claim,
    renew_resume_turn_claim,
    session_sync_targets,
    try_claim_resume_turn,
    try_claim_session_evaluation,
    try_claim_session_sync,
)


@pytest.fixture
def isolated_db(tmp_path):
    original_path = database.DB_PATH
    original_conn = getattr(database._local, "conn", None)
    database.DB_PATH = tmp_path / "resume-turn-claim.db"
    database._local.conn = None
    try:
        database.init_all_tables()
        yield database.DB_PATH
    finally:
        temp_conn = getattr(database._local, "conn", None)
        if temp_conn is not None:
            temp_conn.close()
        database.DB_PATH = original_path
        database._local.conn = original_conn


def _set_claim_time(session_id: str, field: str, value: str) -> None:
    conn = database.get_db()
    conn.execute(
        f"UPDATE sessions SET meta = json_set(meta, '$.{field}', ?) "
        "WHERE session_id = ?",
        (value, session_id),
    )
    conn.commit()


def test_resume_turn_claim_requires_owned_unfinished_resume_session(isolated_db):
    create_session("resume", "resume", user_id="u1")
    create_session("drill", "topic_drill", "python", user_id="u1")
    create_session("complete", "resume", user_id="u1")
    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET review = 'done' WHERE session_id = 'complete'"
    )
    conn.commit()

    token = try_claim_resume_turn("resume", user_id="u1")

    assert token
    assert try_claim_resume_turn("resume", user_id="u1") is None
    assert try_claim_resume_turn("resume", user_id="other") is None
    assert try_claim_resume_turn("drill", user_id="u1") is None
    assert try_claim_resume_turn("complete", user_id="u1") is None


def test_resume_initialization_persists_recoverable_graph_state(isolated_db):
    create_session(
        "initialized",
        "resume",
        meta={"initialization_status": "pending"},
        user_id="u1",
    )

    assert mark_resume_session_initialized("initialized", user_id="u1") is True

    meta = get_session("initialized", user_id="u1")["meta"]
    assert meta["initialization_status"] == "ready"
    assert meta["resume_phase"] == "greeting"
    assert meta["resume_is_finished"] is False


def test_resume_chat_and_retry_normalize_messages_consistently():
    chat = ChatRequest(session_id="session", message="  answer  ")
    retry = RetryInterviewReplyRequest(message="  answer  ")
    forced_retry = RetryInterviewReplyRequest(message="answer", force=True)

    assert chat.message == retry.message == "answer"
    assert retry.force is False
    assert forced_retry.force is True
    completed, reply = interview._completed_resume_reply(
        [
            {"role": "user", "content": "  answer  "},
            {"role": "assistant", "content": "next"},
        ],
        retry.message,
    )
    assert completed is True
    assert reply == "next"


def test_only_one_connection_can_claim_a_resume_turn(isolated_db):
    create_session("concurrent", "resume", user_id="u1")
    barrier = threading.Barrier(2)

    def claim():
        database._local.conn = None
        barrier.wait(timeout=5)
        return try_claim_resume_turn("concurrent", user_id="u1")

    with ThreadPoolExecutor(max_workers=2) as executor:
        tokens = list(executor.map(lambda _index: claim(), range(2)))

    assert sum(token is not None for token in tokens) == 1
    stored = get_session("concurrent", user_id="u1")
    assert stored["meta"]["resume_turn_claim_token"] in tokens


def test_resume_turn_commit_is_token_fenced_and_clears_claim(isolated_db):
    create_session("commit", "resume", user_id="u1")
    token = try_claim_resume_turn("commit", user_id="u1")
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]

    assert commit_resume_turn(
        "commit", messages, user_id="u1", claim_token="wrong",
    ) is False
    assert get_session("commit", user_id="u1")["transcript"] == []

    assert commit_resume_turn(
        "commit", messages, user_id="u1", claim_token=token,
    ) is True
    stored = get_session("commit", user_id="u1")
    assert [message["content"] for message in stored["transcript"]] == [
        "question", "answer",
    ]
    assert all(message.get("time") for message in stored["transcript"])
    assert "resume_turn_claim_token" not in stored["meta"]
    assert commit_resume_turn(
        "commit", messages, user_id="u1", claim_token=token,
    ) is False


def test_resume_turn_commit_persists_completion_state(isolated_db):
    create_session("commit-state", "resume", user_id="u1")
    token = try_claim_resume_turn("commit-state", user_id="u1")

    assert commit_resume_turn(
        "commit-state",
        [
            {"role": "user", "content": "final question"},
            {"role": "assistant", "content": "final reply"},
        ],
        user_id="u1",
        claim_token=token,
        phase="end",
        is_finished=True,
    ) is True

    stored = get_session("commit-state", user_id="u1")
    assert stored["meta"]["resume_phase"] == "end"
    assert stored["meta"]["resume_is_finished"] is True


def test_resume_reply_replacement_is_pair_and_token_fenced(isolated_db):
    create_session("replace", "resume", user_id="u1")
    append_message("replace", "user", "answer", user_id="u1")
    append_message("replace", "assistant", "old reply", user_id="u1")
    token = try_claim_resume_turn("replace", user_id="u1")

    assert replace_resume_reply(
        "replace",
        user_id="u1",
        claim_token=token,
        expected_user_message="different answer",
        assistant_message="new reply",
    ) is False
    assert replace_resume_reply(
        "replace",
        user_id="u1",
        claim_token="wrong",
        expected_user_message="answer",
        assistant_message="new reply",
    ) is False
    assert replace_resume_reply(
        "replace",
        user_id="u1",
        claim_token=token,
        expected_user_message="answer",
        assistant_message="new reply",
        phase="technical",
        is_finished=False,
    ) is True

    stored = get_session("replace", user_id="u1")
    assert [item["content"] for item in stored["transcript"]] == [
        "answer", "new reply",
    ]
    assert stored["meta"]["resume_phase"] == "technical"
    assert stored["meta"]["resume_is_finished"] is False
    assert "resume_turn_claim_token" not in stored["meta"]


def test_resume_turn_commit_rejects_session_completed_after_claim(isolated_db):
    create_session("completed-late", "resume", user_id="u1")
    token = try_claim_resume_turn("completed-late", user_id="u1")
    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET review = 'done' WHERE session_id = 'completed-late'"
    )
    conn.commit()

    assert commit_resume_turn(
        "completed-late",
        [{"role": "user", "content": "late"}],
        user_id="u1",
        claim_token=token,
    ) is False
    assert get_session("completed-late", user_id="u1")["transcript"] == []


def test_evaluation_rejects_active_turn_and_fences_expired_owner(isolated_db):
    create_session("eval", "resume", user_id="u1")
    turn_token = try_claim_resume_turn("eval", user_id="u1")

    assert try_claim_session_evaluation("eval", user_id="u1") is None

    _set_claim_time(
        "eval", "resume_turn_claimed_at", "2000-01-01T00:00:00",
    )
    evaluation_token = try_claim_session_evaluation("eval", user_id="u1")

    assert evaluation_token
    stored = get_session("eval", user_id="u1")
    assert "resume_turn_claim_token" not in stored["meta"]
    assert commit_resume_turn(
        "eval",
        [{"role": "user", "content": "stale"}],
        user_id="u1",
        claim_token=turn_token,
    ) is False
    assert release_resume_turn_claim(
        "eval", user_id="u1", claim_token=turn_token,
    ) is False


def test_evaluation_rejects_resume_transcript_with_pending_user(isolated_db):
    create_session("pending-eval", "resume", user_id="u1")
    assert append_message(
        "pending-eval", "user", "answer", user_id="u1",
    ) is True

    assert try_claim_session_evaluation("pending-eval", user_id="u1") is None

    assert append_message(
        "pending-eval", "assistant", "reply", user_id="u1",
    ) is True
    token = try_claim_session_evaluation("pending-eval", user_id="u1")
    assert token


def test_resume_turn_renewal_keeps_evaluation_fenced(isolated_db):
    create_session("renew", "resume", user_id="u1")
    turn_token = try_claim_resume_turn("renew", user_id="u1")
    _set_claim_time(
        "renew", "resume_turn_claimed_at", "2000-01-01T00:00:00",
    )

    assert renew_resume_turn_claim(
        "renew", user_id="u1", claim_token="wrong",
    ) is False
    assert renew_resume_turn_claim(
        "renew", user_id="u1", claim_token=turn_token,
    ) is True
    renewed = get_session("renew", user_id="u1")
    assert renewed["meta"]["resume_turn_claimed_at"] > "2000-01-01T00:00:00"
    assert try_claim_session_evaluation("renew", user_id="u1") is None

    database.get_db().execute(
        "UPDATE sessions SET review = 'done' WHERE session_id = 'renew'"
    )
    database.get_db().commit()
    assert renew_resume_turn_claim(
        "renew", user_id="u1", claim_token=turn_token,
    ) is False


def test_sync_rejects_active_turn_and_fences_expired_owner(isolated_db):
    create_session("sync", "resume", user_id="u1")
    turn_token = try_claim_resume_turn("sync", user_id="u1")

    assert try_claim_session_sync("sync", user_id="u1") is None

    _set_claim_time(
        "sync", "resume_turn_claimed_at", "2000-01-01T00:00:00",
    )
    sync_token = try_claim_session_sync("sync", user_id="u1")

    assert sync_token
    assert commit_resume_turn(
        "sync",
        [{"role": "user", "content": "stale"}],
        user_id="u1",
        claim_token=turn_token,
    ) is False
    assert release_resume_turn_claim(
        "sync", user_id="u1", claim_token=turn_token,
    ) is False


def test_resume_turn_takes_over_expired_evaluation_claim(isolated_db):
    session_id = "stale-evaluation"
    create_session(session_id, "resume", user_id="u1")
    stale_token = try_claim_session_evaluation(session_id, user_id="u1")
    _set_claim_time(
        session_id, "evaluation_claimed_at", "2000-01-01T00:00:00",
    )

    turn_token = try_claim_resume_turn(session_id, user_id="u1")

    assert turn_token
    stored = get_session(session_id, user_id="u1")
    assert "evaluation_claim_token" not in stored["meta"]
    assert release_session_evaluation_claim(
        session_id, user_id="u1", claim_token=stale_token,
    ) is False


def test_resume_turn_rejects_pending_or_partially_applied_sync(isolated_db):
    create_session("pending", "resume", user_id="u1")
    pending_token = try_claim_session_sync("pending", user_id="u1")
    _set_claim_time("pending", "sync_claimed_at", "2000-01-01T00:00:00")

    assert try_claim_resume_turn("pending", user_id="u1") is None
    assert release_session_sync_claim(
        "pending", user_id="u1", claim_token=pending_token,
    ) is True
    assert try_claim_resume_turn("pending", user_id="u1") is None

    create_session("partial", "resume", user_id="u1")
    partial_token = try_claim_session_sync("partial", user_id="u1")
    assert mark_session_sync_step(
        "partial", "profile", user_id="u1", claim_token=partial_token,
    ) is True
    assert release_session_sync_claim(
        "partial", user_id="u1", claim_token=partial_token,
    ) is True
    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET meta = json_remove(meta, '$.sync_pending_at') "
        "WHERE session_id = 'partial'"
    )
    conn.commit()
    assert try_claim_resume_turn("partial", user_id="u1") is None


def test_sync_targets_are_frozen_atomically_with_claim_and_reused(isolated_db):
    create_session("targets", "resume", user_id="u1")

    first_token = try_claim_session_sync(
        "targets",
        user_id="u1",
        target_group="knowledge",
        target_topics=[" python ", "", "python", "go"],
    )

    assert session_sync_targets(
        "targets", "knowledge", user_id="u1", claim_token=first_token,
    ) == ["python", "go"]
    assert release_session_sync_claim(
        "targets", user_id="u1", claim_token=first_token,
    ) is True

    retry_token = try_claim_session_sync(
        "targets",
        user_id="u1",
        target_group="knowledge",
        target_topics=["changed"],
    )
    assert session_sync_targets(
        "targets", "knowledge", user_id="u1", claim_token=retry_token,
    ) == ["python", "go"]
    assert session_sync_targets(
        "targets", "knowledge", user_id="u1", claim_token=first_token,
    ) == []


def test_empty_sync_target_list_is_still_frozen(isolated_db):
    create_session("empty-targets", "resume", user_id="u1")
    first_token = try_claim_session_sync(
        "empty-targets",
        user_id="u1",
        target_group="knowledge",
        target_topics=["", 1, None],
    )
    assert session_sync_targets(
        "empty-targets", "knowledge", user_id="u1", claim_token=first_token,
    ) == []
    assert release_session_sync_claim(
        "empty-targets", user_id="u1", claim_token=first_token,
    ) is True

    retry_token = try_claim_session_sync(
        "empty-targets",
        user_id="u1",
        target_group="knowledge",
        target_topics=["must-not-appear"],
    )
    assert session_sync_targets(
        "empty-targets", "knowledge", user_id="u1", claim_token=retry_token,
    ) == []
    stored = get_session("empty-targets", user_id="u1")
    assert stored["meta"]["sync_targets"]["knowledge"] == []


def test_first_target_freeze_preserves_legacy_completed_topics(isolated_db):
    create_session("legacy-targets", "resume", user_id="u1")
    conn = database.get_db()
    conn.execute(
        "UPDATE sessions SET meta = json_set(meta, '$.sync_steps', "
        "json_object('knowledge_extract:legacy', 'done')) "
        "WHERE session_id = 'legacy-targets'"
    )
    conn.commit()

    token = try_claim_session_sync(
        "legacy-targets",
        user_id="u1",
        target_group="knowledge",
        target_topics=["new"],
    )

    assert session_sync_targets(
        "legacy-targets", "knowledge", user_id="u1", claim_token=token,
    ) == ["new", "legacy"]


def test_abort_sync_claim_only_before_first_step(isolated_db):
    create_session("abort-fresh", "resume", user_id="u1")
    fresh_token = try_claim_session_sync(
        "abort-fresh",
        user_id="u1",
        target_group="knowledge",
        target_topics=["python"],
    )

    assert abort_session_sync_claim(
        "abort-fresh", user_id="u1", claim_token="wrong",
    ) is False
    assert abort_session_sync_claim(
        "abort-fresh", user_id="u1", claim_token=fresh_token,
    ) is True
    aborted = get_session("abort-fresh", user_id="u1")
    assert "sync_claim_token" not in aborted["meta"]
    assert "sync_claimed_at" not in aborted["meta"]
    assert "sync_pending_at" not in aborted["meta"]
    assert aborted["meta"]["sync_targets"]["knowledge"] == ["python"]

    create_session("abort-started", "resume", user_id="u1")
    started_token = try_claim_session_sync("abort-started", user_id="u1")
    assert mark_session_sync_step(
        "abort-started", "profile", user_id="u1", claim_token=started_token,
    ) is True
    assert abort_session_sync_claim(
        "abort-started", user_id="u1", claim_token=started_token,
    ) is False
    assert release_session_sync_claim(
        "abort-started", user_id="u1", claim_token=started_token,
    ) is True
    started = get_session("abort-started", user_id="u1")
    assert started["meta"].get("sync_pending_at")


def test_concurrent_sync_target_freeze_has_one_stable_winner(isolated_db):
    create_session("target-race", "resume", user_id="u1")
    # Both callers intentionally share the same current token, modelling two
    # processes resuming the same claimed operation.
    token = try_claim_session_sync("target-race", user_id="u1")
    assert release_session_sync_claim(
        "target-race", user_id="u1", claim_token=token,
    ) is True
    barrier = threading.Barrier(2)

    def reclaim(target):
        database._local.conn = None
        barrier.wait(timeout=5)
        claim_token = try_claim_session_sync(
            "target-race",
            user_id="u1",
            target_group="knowledge",
            target_topics=[target],
        )
        if not claim_token:
            return None, []
        return claim_token, session_sync_targets(
            "target-race", "knowledge", user_id="u1", claim_token=claim_token,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reclaim, ["python", "go"]))

    winners = [result for result in results if result[0]]
    assert len(winners) == 1
    assert winners[0][1] in (["python"], ["go"])


def test_resume_retry_replays_committed_reply_without_invoking_graph(monkeypatch):
    class Graph:
        def invoke(self, *_args, **_kwargs):
            raise AssertionError("a durable reply must be replayed, not regenerated")

        def get_state(self, _config):
            return SimpleNamespace(values={"phase": "technical", "is_finished": False})

    entry = {
        "graph": Graph(),
        "config": {"configurable": {"thread_id": "replay"}},
    }
    monkeypatch.setattr(
        interview,
        "get_session",
        lambda *args, **kwargs: {
            "mode": "resume",
            "transcript": [
                {"role": "user", "content": "answer"},
                {"role": "assistant", "content": "next question"},
            ],
        },
    )

    result, message = asyncio.run(
        interview._run_resume_retry(entry, "replay", "answer", "u1"),
    )

    assert result["_recovered"] is True
    assert result["phase"] == "technical"
    assert message == "next question"


def test_manual_regeneration_replaces_graph_reply_without_advancing_twice():
    config = {"configurable": {"thread_id": "regenerate"}}
    pre_ask_values = {
        "messages": [
            AIMessage(content="old question", id="question-1"),
            HumanMessage(content="answer", id="answer-1"),
        ],
        "phase": "technical",
        "questions_asked": ["old question"],
        "phase_question_count": 1,
        "is_finished": False,
        "last_eval": {"score": 7},
        "eval_history": [{"score": 7}],
    }
    latest_values = {
        **pre_ask_values,
        "messages": [
            *pre_ask_values["messages"],
            AIMessage(content="first reply", id="reply-1"),
        ],
        "questions_asked": ["old question", "first reply"],
        "phase_question_count": 2,
        "last_eval": {"score": 5},
        "eval_history": [{"score": 7}, {"score": 5}],
    }

    class AskNode:
        def invoke(self, state, node_config):
            assert node_config == config
            assert state["phase_question_count"] == 1
            assert isinstance(state["messages"][-1], HumanMessage)
            return {
                "messages": [AIMessage(content="replacement reply")],
                "questions_asked": ["old question", "replacement reply"],
                "phase_question_count": 2,
                "last_eval": {"score": 9},
                "eval_history": [{"score": 7}, {"score": 9}],
            }

    class Graph:
        nodes = {"ask": AskNode()}

        def __init__(self):
            self.values = latest_values

        def get_state(self, _config):
            return SimpleNamespace(values=self.values)

        def get_state_history(self, _config, limit):
            assert limit == 100
            return iter([
                SimpleNamespace(values=self.values, next=("wait",)),
                SimpleNamespace(values=pre_ask_values, next=("ask",)),
            ])

        def update_state(self, _config, updates, as_node):
            assert as_node == "ask"
            replacement = updates["messages"][0]
            assert replacement.id == "reply-1"
            self.values = {
                **self.values,
                **updates,
                "messages": [*self.values["messages"][:-1], replacement],
            }

    result, message = interview._regenerate_resume_graph_reply(
        {"graph": Graph(), "config": config}, "answer",
    )

    assert message == "replacement reply"
    assert result["phase_question_count"] == 2
    assert result["questions_asked"] == ["old question", "replacement reply"]
    assert result["eval_history"] == [{"score": 7}, {"score": 9}]
    assert len(result["messages"]) == 3
    assert result["messages"][-1].content == "replacement reply"


def test_manual_regeneration_uses_langgraph_replacement_semantics():
    ask_count = 0

    def initialize(_state):
        return {
            "messages": [AIMessage(content="opening")],
            "phase": "technical",
            "questions_asked": [],
            "phase_question_count": 0,
            "is_finished": False,
            "last_eval": {},
            "eval_history": [],
        }

    def wait_for_answer(_state):
        return {}

    def ask(state):
        nonlocal ask_count
        ask_count += 1
        reply = f"reply {ask_count}"
        return {
            "messages": [AIMessage(content=reply)],
            "questions_asked": [*state["questions_asked"], reply],
            "phase_question_count": state["phase_question_count"] + 1,
        }

    graph_builder = StateGraph(ResumeInterviewState)
    graph_builder.add_node("init", initialize)
    graph_builder.add_node("wait", wait_for_answer)
    graph_builder.add_node("ask", ask)
    graph_builder.add_edge(START, "init")
    graph_builder.add_edge("init", "wait")
    graph_builder.add_edge("ask", "wait")
    graph_builder.add_conditional_edges(
        "wait", lambda _state: "ask", {"ask": "ask"},
    )
    graph = graph_builder.compile(
        checkpointer=InMemorySaver(), interrupt_before=["wait"],
    )
    config = {"configurable": {"thread_id": "real-regenerate"}}
    graph.invoke({}, config)
    graph.update_state(config, {"messages": [HumanMessage(content="answer")]})
    graph.invoke(None, config)

    before = graph.get_state(config).values
    assert before["phase_question_count"] == 1
    assert len(before["messages"]) == 3

    result, message = interview._regenerate_resume_graph_reply(
        {"graph": graph, "config": config}, "answer",
    )

    assert message == "reply 2"
    assert result["phase_question_count"] == 1
    assert result["questions_asked"] == ["reply 2"]
    assert len(result["messages"]) == 3
    assert result["messages"][-1].content == "reply 2"


def test_resume_retry_continues_pending_checkpoint_without_duplicate_user(monkeypatch):
    calls = []

    class Graph:
        def invoke(self, value, config):
            calls.append(("invoke", value, config))
            return {
                "messages": [
                    HumanMessage(content="answer"),
                    AIMessage(content="retried question"),
                ],
                "phase": "technical",
            }

    entry = {
        "graph": Graph(),
        "config": {"configurable": {"thread_id": "pending"}},
    }
    monkeypatch.setattr(
        interview,
        "get_session",
        lambda *args, **kwargs: {
            "mode": "resume",
            "transcript": [{"role": "user", "content": "answer"}],
        },
    )
    monkeypatch.setattr(
        interview, "try_claim_resume_turn", lambda *args, **kwargs: "turn-token",
    )
    monkeypatch.setattr(
        interview, "renew_resume_turn_claim", lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        interview, "release_resume_turn_claim", lambda *args, **kwargs: True,
    )

    committed = []

    def commit(_session_id, messages, **kwargs):
        committed.append(messages)
        return True

    monkeypatch.setattr(interview, "commit_resume_turn", commit)

    result, message = asyncio.run(
        interview._run_resume_retry(entry, "pending", "answer", "u1"),
    )

    assert result["phase"] == "technical"
    assert message == "retried question"
    assert calls == [
        ("invoke", None, {"configurable": {"thread_id": "pending"}}),
    ]
    assert committed == [[{"role": "assistant", "content": "retried question"}]]


def test_resume_retry_rejects_result_without_new_assistant(monkeypatch):
    class Graph:
        def invoke(self, _value, _config):
            return {
                "messages": [
                    AIMessage(content="older reply"),
                    HumanMessage(content="answer"),
                ],
                "phase": "technical",
            }

    entry = {
        "graph": Graph(),
        "config": {"configurable": {"thread_id": "missing-reply"}},
    }
    monkeypatch.setattr(
        interview,
        "get_session",
        lambda *args, **kwargs: {
            "mode": "resume",
            "transcript": [{"role": "user", "content": "answer"}],
        },
    )
    monkeypatch.setattr(
        interview, "try_claim_resume_turn", lambda *args, **kwargs: "turn-token",
    )
    monkeypatch.setattr(
        interview, "renew_resume_turn_claim", lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        interview, "release_resume_turn_claim", lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        interview,
        "commit_resume_turn",
        lambda *args, **kwargs: pytest.fail("an empty/stale reply must not commit"),
    )

    with pytest.raises(RuntimeError, match="no new assistant reply"):
        asyncio.run(
            interview._run_resume_retry(
                entry, "missing-reply", "answer", "u1",
            ),
        )


def test_resume_retry_submits_preserved_message_when_original_never_arrived(monkeypatch):
    calls = []

    class Graph:
        def update_state(self, config, value):
            calls.append(("update", config, value["messages"][0].content))

        def invoke(self, value, config):
            calls.append(("invoke", value, config))
            return {
                "messages": [
                    AIMessage(content="opening"),
                    HumanMessage(content="preserved answer"),
                    AIMessage(content="next question"),
                ],
            }

    entry = {
        "graph": Graph(),
        "config": {"configurable": {"thread_id": "not-arrived"}},
    }
    monkeypatch.setattr(
        interview,
        "get_session",
        lambda *args, **kwargs: {
            "mode": "resume",
            "transcript": [{"role": "assistant", "content": "opening"}],
        },
    )
    monkeypatch.setattr(
        interview, "try_claim_resume_turn", lambda *args, **kwargs: "turn-token",
    )
    monkeypatch.setattr(
        interview, "renew_resume_turn_claim", lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        interview, "release_resume_turn_claim", lambda *args, **kwargs: True,
    )
    committed = []
    monkeypatch.setattr(
        interview,
        "commit_resume_turn",
        lambda _sid, messages, **kwargs: committed.append(messages) or True,
    )

    _result, message = asyncio.run(
        interview._run_resume_retry(
            entry, "not-arrived", "preserved answer", "u1",
        ),
    )

    assert message == "next question"
    assert calls == [
        (
            "update",
            {"configurable": {"thread_id": "not-arrived"}},
            "preserved answer",
        ),
        ("invoke", None, {"configurable": {"thread_id": "not-arrived"}}),
    ]
    assert committed == [[
        {"role": "user", "content": "preserved answer"},
        {"role": "assistant", "content": "next question"},
    ]]


def test_resume_chat_rejects_new_input_while_reply_is_pending(monkeypatch):
    class Graph:
        def update_state(self, *_args, **_kwargs):
            raise AssertionError("pending turns must use the recovery endpoint")

    entry = {
        "graph": Graph(),
        "config": {"configurable": {"thread_id": "pending-chat"}},
    }
    monkeypatch.setattr(
        interview,
        "get_session",
        lambda *args, **kwargs: {
            "mode": "resume",
            "transcript": [{"role": "user", "content": "first answer"}],
        },
    )

    with pytest.raises(RuntimeError, match="pending recovery"):
        asyncio.run(
            interview._commit_resume_turn(
                entry, "pending-chat", "second answer", "u1",
            ),
        )


def test_resume_chat_rejects_input_after_graph_completion(monkeypatch):
    class Graph:
        def get_state(self, _config):
            return SimpleNamespace(values={"phase": "end", "is_finished": True})

        def update_state(self, *_args, **_kwargs):
            raise AssertionError("completed interviews cannot accept new input")

    entry = {
        "graph": Graph(),
        "config": {"configurable": {"thread_id": "finished-chat"}},
    }
    monkeypatch.setattr(
        interview,
        "get_session",
        lambda *args, **kwargs: {
            "mode": "resume",
            "transcript": [{"role": "assistant", "content": "final reply"}],
        },
    )

    with pytest.raises(RuntimeError, match="already complete"):
        asyncio.run(
            interview._commit_resume_turn(
                entry, "finished-chat", "late answer", "u1",
            ),
        )


def test_withdraw_resume_user_tail_removes_pending_message(isolated_db):
    from backend.storage.sessions import withdraw_resume_user_tail

    create_session("withdraw-1", "resume", None, user_id="u1")
    mark_resume_session_initialized("withdraw-1", user_id="u1")
    append_message("withdraw-1", "assistant", "Q1?", user_id="u1")
    append_message("withdraw-1", "user", "my failed answer", user_id="u1")

    token = try_claim_resume_turn("withdraw-1", user_id="u1")
    assert token
    assert withdraw_resume_user_tail(
        "withdraw-1", user_id="u1", claim_token=token,
        expected_message="my failed answer",
    )
    session = get_session("withdraw-1", user_id="u1")
    transcript = session["transcript"]
    assert [m["role"] for m in transcript] == ["assistant"]
    # The successful withdraw releases the claim in the same transaction.
    assert try_claim_resume_turn("withdraw-1", user_id="u1")


def test_withdraw_resume_user_tail_rejects_completed_turn(isolated_db):
    from backend.storage.sessions import withdraw_resume_user_tail

    create_session("withdraw-2", "resume", None, user_id="u1")
    mark_resume_session_initialized("withdraw-2", user_id="u1")
    append_message("withdraw-2", "user", "answer", user_id="u1")
    append_message("withdraw-2", "assistant", "reply landed", user_id="u1")

    token = try_claim_resume_turn("withdraw-2", user_id="u1")
    assert token
    # Tail is an assistant reply — nothing to withdraw; transcript untouched.
    assert not withdraw_resume_user_tail(
        "withdraw-2", user_id="u1", claim_token=token,
        expected_message="answer",
    )
    session = get_session("withdraw-2", user_id="u1")
    assert [m["role"] for m in session["transcript"]] == ["user", "assistant"]
    release_resume_turn_claim("withdraw-2", user_id="u1", claim_token=token)


def test_withdraw_resume_user_tail_rejects_mismatched_message(isolated_db):
    from backend.storage.sessions import withdraw_resume_user_tail

    create_session("withdraw-3", "resume", None, user_id="u1")
    mark_resume_session_initialized("withdraw-3", user_id="u1")
    append_message("withdraw-3", "user", "actual pending answer", user_id="u1")

    token = try_claim_resume_turn("withdraw-3", user_id="u1")
    assert token
    assert not withdraw_resume_user_tail(
        "withdraw-3", user_id="u1", claim_token=token,
        expected_message="some other text",
    )
    session = get_session("withdraw-3", user_id="u1")
    assert [m["role"] for m in session["transcript"]] == ["user"]
    release_resume_turn_claim("withdraw-3", user_id="u1", claim_token=token)


def test_withdraw_resume_user_tail_requires_owned_claim(isolated_db):
    from backend.storage.sessions import withdraw_resume_user_tail

    create_session("withdraw-4", "resume", None, user_id="u1")
    mark_resume_session_initialized("withdraw-4", user_id="u1")
    append_message("withdraw-4", "user", "pending", user_id="u1")

    assert not withdraw_resume_user_tail(
        "withdraw-4", user_id="u1", claim_token="not-the-token",
        expected_message="pending",
    )
    session = get_session("withdraw-4", user_id="u1")
    assert [m["role"] for m in session["transcript"]] == ["user"]
