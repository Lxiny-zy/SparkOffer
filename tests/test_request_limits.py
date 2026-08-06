import asyncio

from fastapi import FastAPI

from backend.utils.request_limits import RequestBodyLimitMiddleware


async def _run_request(messages, *, headers=None, limit=4):
    called = False
    sent = []
    queue = list(messages)

    async def app(_scope, receive, send):
        nonlocal called
        called = True
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return queue.pop(0)

    async def send(message):
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(app, max_bytes=limit)
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": headers or [],
        },
        receive,
        send,
    )
    return called, sent


def test_content_length_over_limit_is_rejected_before_endpoint():
    called, sent = asyncio.run(_run_request(
        [{"type": "http.request", "body": b"", "more_body": False}],
        headers=[(b"content-length", b"5")],
    ))

    assert called is False
    assert sent[0]["status"] == 413


def test_chunked_body_over_limit_is_rejected():
    called, sent = asyncio.run(_run_request([
        {"type": "http.request", "body": b"abc", "more_body": True},
        {"type": "http.request", "body": b"de", "more_body": False},
    ]))

    assert called is True
    assert sent[0]["status"] == 413


def test_fastapi_chunked_json_over_limit_returns_413():
    app = FastAPI()
    called = False

    @app.post("/")
    async def endpoint(payload: dict):
        nonlocal called
        called = True
        return payload

    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=4)
    messages = [
        {"type": "http.request", "body": b'{"a"', "more_body": True},
        {"type": "http.request", "body": b":1}", "more_body": False},
    ]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    asyncio.run(app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        },
        receive,
        send,
    ))

    assert called is False
    assert sent[0]["status"] == 413
