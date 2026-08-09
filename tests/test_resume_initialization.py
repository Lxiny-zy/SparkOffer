import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage

from backend.graphs import resume_interview
from backend.models import InterviewMode, StartInterviewRequest
from backend.routers import interview, profile


class BlockingInitialGraph:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def invoke(self, _state, _config):
        self.started.set()
        assert self.release.wait(timeout=2)
        return {"messages": [AIMessage(content="first question")]}


def test_resume_initialization_finishes_after_client_disconnect(monkeypatch):
    graph = BlockingInitialGraph()
    calls = []
    committed = threading.Event()
    monkeypatch.setattr(interview, "new_session_id", lambda: "resume-init")
    monkeypatch.setattr(interview, "compile_resume_interview", lambda _user_id: graph)
    monkeypatch.setattr(
        interview,
        "create_session",
        lambda *args, **kwargs: calls.append(("create", args, kwargs)),
    )
    monkeypatch.setattr(
        interview,
        "save_live_session",
        lambda *args, **kwargs: calls.append(("live", args, kwargs)),
    )
    monkeypatch.setattr(
        interview,
        "append_message",
        lambda *args, **kwargs: calls.append(("append", args, kwargs)) or True,
    )
    monkeypatch.setattr(
        interview,
        "mark_resume_session_initialized",
        lambda *args, **kwargs: committed.set() or True,
    )
    monkeypatch.setattr(
        interview,
        "delete_session",
        lambda *args, **kwargs: calls.append(("delete", args, kwargs)) or True,
    )

    async def disconnect():
        response = await interview.start_interview(
            StartInterviewRequest(mode=InterviewMode.RESUME),
            user_id="user-1",
        )
        iterator = response.body_iterator
        first = await iterator.__anext__()
        assert '"type": "progress"' in first
        next_chunk = asyncio.create_task(iterator.__anext__())
        assert await asyncio.to_thread(graph.started.wait, 1)
        next_chunk.cancel()
        try:
            await next_chunk
        except asyncio.CancelledError:
            pass
        graph.release.set()
        assert await asyncio.to_thread(committed.wait, 1)

    try:
        asyncio.run(disconnect())
        assert any(call[0] == "append" for call in calls)
        assert not any(call[0] == "delete" for call in calls)
    finally:
        interview.graphs.pop("resume-init", None)


def test_resume_session_restore_includes_graph_completion_state(monkeypatch):
    class Graph:
        def get_state(self, config):
            assert config == {"configurable": {"thread_id": "resume-state"}}
            return SimpleNamespace(values={"phase": "end", "is_finished": True})

    monkeypatch.setattr(
        profile,
        "get_session",
        lambda *args, **kwargs: {
            "session_id": "resume-state",
            "mode": "resume",
            "review": "",
            "transcript": [],
        },
    )
    monkeypatch.setattr(
        resume_interview,
        "compile_resume_interview",
        lambda _user_id: Graph(),
    )

    restored = asyncio.run(
        profile.get_interview_session("resume-state", user_id="u1"),
    )

    assert restored["resume_state"] == {
        "phase": "end",
        "is_finished": True,
        "recoverable": True,
    }


def test_resume_session_restore_prefers_durable_completion_state(monkeypatch):
    monkeypatch.setattr(
        profile,
        "get_session",
        lambda *args, **kwargs: {
            "session_id": "durable-state",
            "mode": "resume",
            "review": "",
            "transcript": [],
            "meta": {
                "resume_phase": "end",
                "resume_is_finished": True,
            },
        },
    )
    monkeypatch.setattr(
        resume_interview,
        "compile_resume_interview",
        lambda _user_id: pytest.fail("durable state must avoid checkpoint reads"),
    )

    restored = asyncio.run(
        profile.get_interview_session("durable-state", user_id="u1"),
    )

    assert restored["resume_state"] == {
        "phase": "end",
        "is_finished": True,
    }


def test_resume_session_restore_flags_lost_checkpoint_unrecoverable(monkeypatch):
    class EmptyGraph:
        def get_state(self, config):
            return SimpleNamespace(values={})

    monkeypatch.setattr(
        profile,
        "get_session",
        lambda *args, **kwargs: {
            "session_id": "lost-checkpoint",
            "mode": "resume",
            "review": "",
            "transcript": [],
            "meta": {},
        },
    )
    monkeypatch.setattr(
        resume_interview,
        "compile_resume_interview",
        lambda _user_id: EmptyGraph(),
    )

    restored = asyncio.run(
        profile.get_interview_session("lost-checkpoint", user_id="u1"),
    )

    assert restored["resume_state"] == {
        "phase": None,
        "is_finished": False,
        "recoverable": False,
    }


def test_resume_session_restore_reports_checkpoint_failure(monkeypatch):
    monkeypatch.setattr(
        profile,
        "get_session",
        lambda *args, **kwargs: {
            "session_id": "broken-state",
            "mode": "resume",
            "review": "",
            "transcript": [],
            "meta": {},
        },
    )
    monkeypatch.setattr(
        resume_interview,
        "compile_resume_interview",
        lambda _user_id: (_ for _ in ()).throw(RuntimeError("checkpoint unavailable")),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            profile.get_interview_session("broken-state", user_id="u1"),
        )

    assert exc_info.value.status_code == 503
