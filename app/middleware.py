"""Pure ASGI middlewares (no response buffering, so SSE streams flow through)."""

import logging

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.db import query_counter

log = logging.getLogger("studyhub.queries")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Cross-Origin-Opener-Policy": "same-origin",
        }
        if hsts:
            self.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
            # JSON API only: nothing should ever render or embed it.
            self.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in self.headers.items():
                    headers.setdefault(k, v)
            await send(message)

        await self.app(scope, receive, send_wrapper)


class QueryCountMiddleware:
    """Dev-only N+1 guard: counts SQL statements per request, exposes the count
    in X-Query-Count and logs a warning when it exceeds the threshold."""

    def __init__(self, app: ASGIApp, *, threshold: int) -> None:
        self.app = app
        self.threshold = threshold

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        counter = [0]
        token = query_counter.set(counter)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Query-Count"] = str(counter[0])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            query_counter.reset(token)
            if counter[0] > self.threshold:
                log.warning(
                    "N+1 suspect: %s %s ran %d queries (threshold %d)",
                    scope.get("method"),
                    scope.get("path"),
                    counter[0],
                    self.threshold,
                )
