import asyncio
import time

from backend.utils.sse_helpers import stream_blocking_sse


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
