import asyncio
import time
from types import SimpleNamespace

import pytest

from backend.utils.sse_helpers import (
    iter_llm_stream,
    sse_event,
    streaming_response,
    stream_awaitable_sse,
    stream_blocking_sse,
)


def _decode_event(frame: str) -> dict:
    import json
    return json.loads(frame[6:].strip())


def test_sse_event_marks_terminal_outcomes():
    assert _decode_event(sse_event({"type": "complete", "data": 1}))["terminal"] == "success"
    assert _decode_event(sse_event({"type": "error", "message": "nope"}))["terminal"] == "error"


def test_sse_event_cannot_mark_error_as_success():
    assert _decode_event(
        sse_event({"type": "error", "terminal": "success", "message": "nope"})
    )["terminal"] == "error"


def test_streaming_response_repairs_missing_error_terminator():
    async def source():
        yield sse_event({"type": "error", "message": "failed"})

    async def collect():
        return [frame async for frame in streaming_response(source()).body_iterator]

    events = [_decode_event(frame) for frame in asyncio.run(collect())]
    assert [event["type"] for event in events] == ["error", "done"]
    assert events[-1]["terminal"] == "error"


def test_streaming_response_repairs_missing_success_terminator():
    async def source():
        yield sse_event({"type": "complete", "data": {"ok": True}})

    async def collect():
        return [frame async for frame in streaming_response(source()).body_iterator]

    events = [_decode_event(frame) for frame in asyncio.run(collect())]
    assert [event["type"] for event in events] == ["complete", "done"]
    assert events[-1]["terminal"] == "success"


def test_streaming_response_turns_premature_eof_into_error_terminal():
    async def source():
        yield sse_event({"type": "progress", "message": "working"})

    async def collect():
        return [frame async for frame in streaming_response(source()).body_iterator]

    events = [_decode_event(frame) for frame in asyncio.run(collect())]
    assert [event["type"] for event in events][-2:] == ["error", "done"]
    assert events[-1]["terminal"] == "error"


def test_streaming_response_turns_post_complete_failure_into_error_terminal():
    async def source():
        yield sse_event({"type": "complete", "data": {"ok": True}})
        raise RuntimeError("post-processing failed")

    async def collect():
        return [frame async for frame in streaming_response(source()).body_iterator]

    events = [_decode_event(frame) for frame in asyncio.run(collect())]
    assert [event["type"] for event in events] == ["error", "done"]
    assert events[-1]["terminal"] == "error"


def test_streaming_response_preserves_done_metadata_for_chat_style_streams():
    async def source():
        yield sse_event({"type": "token", "content": "ok"})
        yield sse_event({"type": "done", "meta": {"saved": True}})

    async def collect():
        return [frame async for frame in streaming_response(source()).body_iterator]

    events = [_decode_event(frame) for frame in asyncio.run(collect())]
    assert events[-1]["type"] == "done"
    assert events[-1]["meta"] == {"saved": True}
    assert events[-1]["terminal"] == "success"


def test_streaming_response_replaces_success_done_when_late_error_arrives():
    async def source():
        yield sse_event({"type": "done"})
        yield sse_event({"type": "error", "message": "failed late"})

    async def collect():
        return [frame async for frame in streaming_response(source()).body_iterator]

    events = [_decode_event(frame) for frame in asyncio.run(collect())]
    assert [event["type"] for event in events] == ["error", "done"]
    assert events[-1]["terminal"] == "error"


def test_streaming_response_replaces_done_when_cleanup_fails():
    async def source():
        yield sse_event({"type": "complete", "data": {"ok": True}})
        yield sse_event({"type": "done"})
        raise RuntimeError("cleanup failed")

    async def collect():
        return [frame async for frame in streaming_response(source()).body_iterator]

    events = [_decode_event(frame) for frame in asyncio.run(collect())]
    assert [event["type"] for event in events] == ["error", "done"]
    assert events[-1]["terminal"] == "error"


def test_blocking_sse_returns_as_soon_as_task_finishes():
    async def collect():
        return [item async for item in stream_blocking_sse(
            lambda: "done", heartbeat_interval=1.0,
        )]

    started = time.monotonic()
    events = asyncio.run(collect())
    elapsed = time.monotonic() - started

    assert events[-1] == ("result", "done")
    assert elapsed < 0.5


def test_blocking_sse_does_not_forward_provider_exception_details():
    async def all_events():
        return [item async for item in stream_blocking_sse(
            lambda: (_ for _ in ()).throw(
                RuntimeError("https://user:secret@provider.example/v1?api_key=secret")
            ),
            heartbeat_interval=0.01,
        )]

    all_items = asyncio.run(all_events())
    error_frames = [value for kind, value in all_items if kind == "sse" and '"type": "error"' in value]
    assert error_frames
    assert "secret" not in error_frames[-1]


def test_awaitable_sse_emits_heartbeats_and_result():
    async def collect():
        async def work():
            await asyncio.sleep(0.03)
            return "done"

        return [item async for item in stream_awaitable_sse(
            work(), heartbeat_interval=0.01, timeout=1.0,
        )]

    events = asyncio.run(collect())

    assert any(kind == "sse" and '"type": "ping"' in value for kind, value in events)
    assert events[-1] == ("result", "done")


def test_awaitable_sse_timeout_cancels_work():
    async def run():
        cancelled = asyncio.Event()

        async def work():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with pytest.raises(TimeoutError, match="operation exceeded"):
            async for _ in stream_awaitable_sse(
                work(), heartbeat_interval=0.005, timeout=0.02,
            ):
                pass
        assert cancelled.is_set()

    asyncio.run(run())


def test_llm_stream_heartbeats_when_gateway_only_emits_empty_chunks():
    class EmptyChunkLLM:
        def astream(self, _messages):
            async def chunks():
                for _ in range(4):
                    await asyncio.sleep(0.01)
                    yield SimpleNamespace(content="", additional_kwargs={})

            return chunks()

    async def collect():
        return [item async for item in iter_llm_stream(
            EmptyChunkLLM(), [], idle_timeout=1.0, keepalive_interval=0.015,
        )]

    events = asyncio.run(collect())

    assert ("idle", "") in events
