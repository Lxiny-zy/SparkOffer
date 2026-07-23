from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, SystemMessage

import backend.assistant as assistant
import backend.qa_arena as qa_arena
from backend.models import AlgorithmChatRequest, AlgorithmSaveRequest
from backend.routers import algorithm


def test_assistant_history_and_favorites_use_items(monkeypatch):
    monkeypatch.setattr(
        assistant,
        "list_sessions",
        lambda **kwargs: {
            "items": [{
                "created_at": "2026-07-22",
                "mode": "topic_drill",
                "topic": "python",
                "avg_score": 0,
            }],
            "total": 1,
        },
    )
    monkeypatch.setattr(
        assistant,
        "list_favorites",
        lambda **kwargs: {
            "items": [{"question": "What is the GIL?"}],
            "total": 1,
        },
    )

    history = asyncio.run(
        assistant._execute_tool("search_history", {}, "user-1"),
    )["data"]
    favorites = asyncio.run(
        assistant._execute_tool("list_favorites", {}, "user-1"),
    )["data"]

    assert "python" in history
    assert "得分 0" in history
    assert "What is the GIL?" in favorites


def test_assistant_knowledge_query_uses_async_thread_offload(monkeypatch):
    called = {}

    async def fake_to_thread(func, *args):
        called["func"] = func
        called["args"] = args
        return ["matched chunk"]

    monkeypatch.setattr(assistant.asyncio, "to_thread", fake_to_thread)
    result = asyncio.run(assistant._execute_tool(
        "query_knowledge_base",
        {"topic": "python", "query": "gil"},
        "user-1",
    ))

    assert called["func"] is assistant.retrieve_topic_context
    assert called["args"] == ("python", "gil", "user-1", 5)
    assert "matched chunk" in result["data"]


def test_assistant_turns_for_one_user_are_serialized(monkeypatch):
    order = []

    async def fake_turn(message, user_id):
        order.append(f"{message}-start")
        if message == "first":
            await release_first.wait()
        yield f"{message}-event"
        order.append(f"{message}-end")

    async def scenario():
        nonlocal release_first
        release_first = asyncio.Event()
        first_started = asyncio.Event()

        async def tracked_turn(message, user_id):
            if message == "first":
                first_started.set()
            async for event in fake_turn(message, user_id):
                yield event

        monkeypatch.setattr(
            assistant, "_stream_assistant_chat_unlocked", tracked_turn,
        )

        async def collect(stream):
            return [event async for event in stream]

        first_task = asyncio.create_task(collect(
            assistant.stream_assistant_chat("first", "user-1"),
        ))
        await first_started.wait()
        second_task = asyncio.create_task(collect(
            assistant.stream_assistant_chat("second", "user-1"),
        ))
        await asyncio.sleep(0)
        assert order == ["first-start"]

        release_first.set()
        assert await first_task == ["first-event"]
        assert await second_task == ["second-event"]

    release_first = None
    asyncio.run(scenario())
    assert order == [
        "first-start", "first-end", "second-start", "second-end",
    ]


def test_welcome_back_uses_session_items_and_keeps_zero_score(monkeypatch):
    monkeypatch.setattr(
        assistant, "_load_profile",
        lambda user_id: {"stats": {"total_sessions": 1, "score_history": []}},
    )
    monkeypatch.setattr(
        assistant, "list_sessions",
        lambda **kwargs: {
            "items": [{
                "topic": "python",
                "avg_score": 0,
                "created_at": "2026-07-22",
            }],
            "total": 1,
        },
    )
    monkeypatch.setattr(assistant, "get_due_reviews", lambda user_id: [])

    message = assistant.generate_welcome_back("user-1")

    assert "python" in message
    assert "0 分" in message


def test_qa_chat_and_regenerate_share_one_turn_lock(monkeypatch):
    order = []

    async def scenario():
        chat_started = asyncio.Event()
        release_chat = asyncio.Event()

        async def fake_chat(*args, **kwargs):
            order.append("chat-start")
            chat_started.set()
            await release_chat.wait()
            yield "chat-event"
            order.append("chat-end")

        async def fake_regenerate(*args, **kwargs):
            order.append("regen-start")
            yield "regen-event"
            order.append("regen-end")

        monkeypatch.setattr(qa_arena, "_stream_qa_chat_unlocked", fake_chat)
        monkeypatch.setattr(
            qa_arena, "_stream_qa_regenerate_unlocked", fake_regenerate,
        )

        async def collect(stream):
            return [event async for event in stream]

        chat_task = asyncio.create_task(collect(
            qa_arena.stream_qa_chat("session-1", "question", "user-1"),
        ))
        await chat_started.wait()
        regen_task = asyncio.create_task(collect(
            qa_arena.stream_qa_regenerate("session-1", "user-1"),
        ))
        await asyncio.sleep(0)
        assert order == ["chat-start"]

        release_chat.set()
        assert await chat_task == ["chat-event"]
        assert await regen_task == ["regen-event"]

    asyncio.run(scenario())
    assert order == ["chat-start", "chat-end", "regen-start", "regen-end"]


def test_algorithm_chats_are_serialized(monkeypatch):
    session = {
        "user_id": "user-1",
        "language": "python",
        "messages": [SystemMessage(content="system"), AIMessage(content="solution")],
        "solution": "solution",
        "problem_text": "problem",
    }
    active = 0
    max_active = 0

    monkeypatch.setattr(algorithm, "get_live", lambda *args: session)
    monkeypatch.setattr(algorithm, "save_live", lambda *args: None)

    async def fake_stream(*args, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        yield "result", "reply"

    monkeypatch.setattr(algorithm, "stream_llm_sse", fake_stream)

    async def scenario():
        first = algorithm.algorithm_chat(
            AlgorithmChatRequest(session_id="s1", message="first"),
            user_id="user-1",
        )
        second = algorithm.algorithm_chat(
            AlgorithmChatRequest(session_id="s1", message="second"),
            user_id="user-1",
        )

        async def consume(response):
            return [part async for part in response.body_iterator]

        await asyncio.gather(consume(first), consume(second))

    asyncio.run(scenario())
    assert max_active == 1


def test_algorithm_save_deletes_persisted_live_session(monkeypatch):
    session = {
        "user_id": "user-1",
        "language": "python",
        "messages": [SystemMessage(content="system"), AIMessage(content="solution")],
        "solution": "solution",
        "problem_text": "problem",
        "source_url": "",
    }
    deleted = []
    monkeypatch.setattr(algorithm, "get_live", lambda *args: session)
    monkeypatch.setattr(algorithm, "_add_algo", lambda **kwargs: {"id": "card-1"})
    monkeypatch.setattr(
        algorithm, "del_live",
        lambda store, session_id, user_id: deleted.append((session_id, user_id)),
    )

    result = asyncio.run(algorithm.algorithm_save(
        AlgorithmSaveRequest(session_id="s1", title="card"),
        user_id="user-1",
    ))

    assert result == {"id": "card-1"}
    assert deleted == [("s1", "user-1")]
