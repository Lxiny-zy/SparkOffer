"""Shared SSE streaming helpers for blocking LLM endpoints."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any, Callable

from fastapi.responses import StreamingResponse

logger = logging.getLogger("uvicorn")

IDLE_HEARTBEAT_SECONDS = 30
PROGRESS_CHAR_INTERVAL = 200
# A reasoning model can stream thinking deltas for minutes with empty visible
# content. We forward batched reasoning at least this often so the SSE stream
# never goes byte-silent (which a proxy/httpx read-timeout would otherwise kill).
REASONING_KEEPALIVE_SECONDS = 3.0


def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def streaming_response(generator: AsyncGenerator[str, None]) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def chunk_text(chunk) -> str:
    """Extract visible answer text from a streamed LangChain chunk, robust to provider quirks.

    ``chunk.content`` is not always a plain string: OpenAI-compatible / reasoning gateways
    stream it as a list of content blocks (``[{"type": "text", "text": ...}]`` or bare
    strings), or as an empty list while the real answer is carried elsewhere. A bare
    ``accumulated += chunk.content`` then raises ``TypeError`` on a list, or silently
    appends nothing on an empty list → blank reply. Reasoning-only deltas (thinking carried
    in ``additional_kwargs``) yield "" here by design — see ``chunk_reasoning``. Never raises.
    """
    content = getattr(chunk, "content", None)
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("text") or block.get("content") or ""
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return ""


def _coerce_reasoning(val) -> str:
    """Best-effort pull a reasoning string out of whatever shape the gateway used."""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        t = val.get("text") or val.get("content") or val.get("summary")
        return t if isinstance(t, str) else ""
    if isinstance(val, list):
        parts: list[str] = []
        for b in val:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                t = b.get("text") or b.get("content") or b.get("summary")
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return ""


def chunk_reasoning(chunk) -> str:
    """Extract the *thinking* (reasoning) delta from a streamed chunk, if the model emits one.

    Reasoning models (gpt-5.x / o-series / DeepSeek-R1) served through an OpenAI-compatible
    gateway stream their thinking in a side channel LangChain drops into ``additional_kwargs``
    (never ``content``) — usually ``reasoning_content`` (DeepSeek-style) or ``reasoning``.
    NOTE: OpenAI reasoning models do NOT expose raw reasoning by default; you only get a
    delta here if the gateway forwards a reasoning summary. Forwarding it keeps the SSE
    stream alive during long thinking and lets the UI show progress. Never raises.
    """
    kw = getattr(chunk, "additional_kwargs", None)
    if not isinstance(kw, dict):
        return ""
    for key in ("reasoning_content", "reasoning"):
        s = _coerce_reasoning(kw.get(key))
        if s:
            return s
    # Defensive: some gateways use a slightly different key (reasoning_text, thinking,
    # thought, ...). Scan for any reasoning-like key carrying text.
    for k, v in kw.items():
        lk = str(k).lower()
        if "reason" in lk or "think" in lk or "thought" in lk:
            s = _coerce_reasoning(v)
            if s:
                return s
    return ""


async def iter_chunks_with_idle(aiter, idle_timeout: float):
    """Iterate an async LLM stream, surfacing idle windows WITHOUT cancelling the read.

    Yields ``("chunk", chunk)`` for each streamed chunk and ``("idle", None)`` whenever
    ``idle_timeout`` seconds pass with no chunk, so the caller can emit a heartbeat ping.

    Why this primitive exists — the obvious idiom is WRONG for an httpx-backed stream::

        chunk = await asyncio.wait_for(aiter.__anext__(), timeout=idle_timeout)  # ✗

    On timeout ``wait_for`` *cancels* the in-flight ``__anext__()``. That cancellation tears
    the underlying httpx read down and finalizes the LangChain async generator, so the NEXT
    ``__anext__()`` ends the stream early (``StopAsyncIteration``) or raises. A reasoning model
    whose first token — or any inter-chunk gap — takes longer than ``idle_timeout`` then gets
    killed mid-flight even though the upstream is perfectly healthy and goes on to complete:
    the client sees an error / empty reply while the provider logs a success (✓ 200 + tokens).

    Here the pull task SURVIVES across idle windows (``asyncio.wait`` does not cancel its
    awaitable on timeout). Liveness is therefore bounded only by httpx's own read timeout — a
    genuinely stalled upstream still fails, a slow-but-alive one (long reasoning) no longer does.
    """
    pull = asyncio.ensure_future(aiter.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({pull}, timeout=idle_timeout)
            if not done:
                yield ("idle", None)          # silent window — the read is STILL in flight
                continue
            try:
                chunk = pull.result()
            except StopAsyncIteration:
                return
            # Schedule the next read before handing this chunk to the caller, so the
            # provider keeps streaming while the caller processes/yields downstream.
            pull = asyncio.ensure_future(aiter.__anext__())
            yield ("chunk", chunk)
    finally:
        # Release the in-flight read + underlying stream on any exit (normal end, mid-stream
        # error, or consumer disconnect). Cancel the pending pull BEFORE aclose() — calling
        # aclose() on a generator that still has a live __anext__() task raises RuntimeError.
        pull.cancel()
        try:
            await asyncio.gather(pull, return_exceptions=True)
        except Exception:
            pass
        aclose = getattr(aiter, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass


async def iter_llm_stream(
    llm, lc_messages, *,
    idle_timeout: float = IDLE_HEARTBEAT_SECONDS,
    keepalive_interval: float = REASONING_KEEPALIVE_SECONDS,
):
    """Reasoning-aware, heartbeat-safe LLM stream → typed delta tuples.

    Yields ``(kind, text)`` where kind is:
      - ``"token"``     visible answer delta (via ``chunk_text``)
      - ``"reasoning"`` thinking delta (via ``chunk_reasoning``), time-batched so a long
                        reasoning phase still emits bytes ~every ``keepalive_interval`` s
      - ``"idle"``      no chunk for ``idle_timeout`` s — caller should emit a ping

    This is the single hardened streaming loop every SSE endpoint should use instead of a
    bare ``async for chunk in llm.astream(...)``: the bare form goes byte-silent during a
    reasoning model's thinking phase (chunks arrive steadily but carry empty ``content``,
    so neither a token nor the idle ping fires) until httpx/nginx times out → blank reply.

    Chunk pulling is delegated to ``iter_chunks_with_idle`` so the idle heartbeat never
    cancels the in-flight read (see that function's docstring for the failure mode it avoids).

    Pass ``keepalive_interval=0`` to forward every reasoning delta unbatched (smooth live
    thinking, e.g. interactive chat). Mid-stream errors propagate to the caller, matching
    ``ResilientChatModel`` (no failover once streaming has begun); the caller decides how to
    surface whatever streamed before the drop.
    """
    aiter = llm.astream(lc_messages).__aiter__()
    rbuf = ""
    last_emit = time.monotonic()
    async for kind, chunk in iter_chunks_with_idle(aiter, idle_timeout):
        if kind == "idle":
            if rbuf:
                yield ("reasoning", rbuf)
                rbuf = ""
            last_emit = time.monotonic()
            yield ("idle", "")
            continue
        r = chunk_reasoning(chunk)
        if r:
            rbuf += r
            if time.monotonic() - last_emit >= keepalive_interval:
                yield ("reasoning", rbuf)
                rbuf = ""
                last_emit = time.monotonic()
        t = chunk_text(chunk)
        if t:
            if rbuf:
                yield ("reasoning", rbuf)
                rbuf = ""
            yield ("token", t)
            last_emit = time.monotonic()
    if rbuf:
        yield ("reasoning", rbuf)


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
        async for kind, delta in iter_llm_stream(llm, lc_messages):
            if kind == "idle":
                yield ("sse", sse_event({"type": "ping"}))
            elif kind == "reasoning":
                # Thinking phase — keep the stream hot and the progress bar alive without
                # dumping raw reasoning into these (non-chat) endpoints.
                yield ("sse", sse_event({"type": "progress", "message": f"{progress_prefix}...（思考中）"}))
            elif kind == "token":
                accumulated += delta
                if stream_content:
                    yield ("sse", sse_event({"type": "content", "delta": delta}))
                chars_since_heartbeat += len(delta)
                if chars_since_heartbeat >= PROGRESS_CHAR_INTERVAL:
                    yield (
                        "sse",
                        sse_event({
                            "type": "progress",
                            "message": f"{progress_prefix}... ({len(accumulated)} 字)",
                        }),
                    )
                    chars_since_heartbeat = 0
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
