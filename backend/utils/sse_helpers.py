"""Shared SSE streaming helpers for blocking LLM endpoints."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any, Callable

from fastapi.responses import StreamingResponse

logger = logging.getLogger("uvicorn")

IDLE_HEARTBEAT_SECONDS = 30
PROGRESS_CHAR_INTERVAL = 200


def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def streaming_response(generator: AsyncGenerator[str, None]) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def stream_llm_sse(
    lc_messages: list,
    *,
    progress_prefix: str = "正在生成中",
    stream_content: bool = False,
) -> AsyncGenerator[tuple[str, str], None]:
    """Stream an LLM call with heartbeat, yielding (event_type, sse_line) pairs.

    Yields SSE lines (progress / ping) during generation. When ``stream_content``
    is True, additionally emits a per-token ``{"type": "content", "delta": ...}``
    SSE event so the caller can render the answer live (真流式)。默认 False，保持
    其它端点依赖的"只推进度计数"行为不变。
    The final yield is ("result", accumulated_text) — NOT an SSE line.
    Caller is responsible for emitting the ``complete`` and ``done`` events
    after any post-processing.

    Usage::

        content = ""
        async for kind, value in stream_llm_sse(messages, progress_prefix="解题"):
            if kind == "sse":
                yield value          # forward heartbeat / progress to client
            else:                    # kind == "result"
                content = value      # full accumulated text

        # post-process content, then:
        yield sse_event({"type": "complete", "data": {...}})
        yield sse_event({"type": "done"})
    """
    from backend.llm_provider import get_langchain_llm

    yield ("sse", sse_event({"type": "progress", "message": f"{progress_prefix}..."}))

    accumulated = ""
    chars_since_heartbeat = 0

    try:
        llm = get_langchain_llm()
        aiter = llm.astream(lc_messages).__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(
                    aiter.__anext__(), timeout=IDLE_HEARTBEAT_SECONDS,
                )
                token = chunk.content if hasattr(chunk, "content") else ""
                if token:
                    accumulated += token
                    if stream_content:
                        yield ("sse", sse_event({"type": "content", "delta": token}))
                    chars_since_heartbeat += len(token)
                    if chars_since_heartbeat >= PROGRESS_CHAR_INTERVAL:
                        yield (
                            "sse",
                            sse_event({
                                "type": "progress",
                                "message": f"{progress_prefix}... ({len(accumulated)} 字)",
                            }),
                        )
                        chars_since_heartbeat = 0
            except asyncio.TimeoutError:
                yield ("sse", sse_event({"type": "ping"}))
            except StopAsyncIteration:
                break
    except Exception as e:
        logger.error("stream_llm_sse failed: %s", e)
        yield ("sse", sse_event({"type": "error", "message": "AI 服务暂时不可用，请稍后重试"}))
        return

    yield ("result", accumulated)


async def stream_blocking_sse(
    sync_callable: Callable[..., Any],
    *args: Any,
    progress_msg: str = "正在处理中",
    heartbeat_interval: float = 5.0,
) -> AsyncGenerator[tuple[str, Any], None]:
    """Run a sync function in a thread pool, yielding heartbeat SSE while waiting.

    Same yield convention as ``stream_llm_sse``:
    - ("sse", sse_line)   — forward to client
    - ("result", value)   — the return value of sync_callable
    """
    yield ("sse", sse_event({"type": "progress", "message": f"{progress_msg}..."}))

    task = asyncio.ensure_future(asyncio.to_thread(sync_callable, *args))

    while not task.done():
        await asyncio.sleep(heartbeat_interval)
        if not task.done():
            yield ("sse", sse_event({"type": "ping"}))

    if task.exception():
        logger.error("stream_blocking_sse failed: %s", task.exception())
        yield ("sse", sse_event({"type": "error", "message": str(task.exception())}))
        return

    yield ("result", task.result())
