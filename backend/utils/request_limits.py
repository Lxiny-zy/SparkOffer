"""ASGI request-size enforcement shared by every deployment mode."""

import json
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int,
        path_limits: dict[str, int] | None = None,
        path_limit_resolver: Callable[[str], int | None] | None = None,
    ) -> None:
        self.app = app
        self.max_bytes = max(1, int(max_bytes))
        # Route-specific limits keep the general JSON API small while allowing
        # the streaming multipart upload endpoints to enforce their documented
        # larger caps. Keys may be exact paths or prefixes ending in ``*``.
        self.path_limits = {
            str(path): max(1, int(limit))
            for path, limit in (path_limits or {}).items()
        }
        self.path_limit_resolver = path_limit_resolver

    def _limit_for_scope(self, scope: Scope) -> int:
        path = scope.get("path", "")
        if self.path_limit_resolver is not None:
            resolved = self.path_limit_resolver(path)
            if resolved is not None:
                return max(1, int(resolved))
        exact = self.path_limits.get(path)
        if exact is not None:
            return exact
        matches = [
            (key[:-1], limit)
            for key, limit in self.path_limits.items()
            if key.endswith("*") and path.startswith(key[:-1])
        ]
        if matches:
            return max(matches, key=lambda item: len(item[0]))[1]
        return self.max_bytes

    async def _reject(self, send: Send) -> None:
        body = json.dumps({"detail": "Request body too large"}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for_scope(scope)
        headers = dict(scope.get("headers") or [])
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                if int(raw_length) > limit:
                    await self._reject(send)
                    return
            except ValueError:
                pass

        received = 0
        response_started = False
        too_large = False
        rejection_sent = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    raise RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started, rejection_sent
            if rejection_sent:
                return
            if too_large and not response_started:
                await self._reject(send)
                rejection_sent = True
                response_started = True
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if not response_started and not rejection_sent:
                await self._reject(send)
