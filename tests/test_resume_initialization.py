import asyncio
import threading

from langchain_core.messages import AIMessage

from backend.models import InterviewMode, StartInterviewRequest
from backend.routers import interview


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
