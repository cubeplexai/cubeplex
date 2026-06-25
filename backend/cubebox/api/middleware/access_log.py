"""Pure ASGI access-log middleware.

Pure ASGI (not ``BaseHTTPMiddleware``) so it never buffers SSE / streaming
responses — the same reason the other middleware here avoid BaseHTTPMiddleware.
Logs one line per HTTP request via loguru, matching the app's log format:

    <client ip> <method> <path?query> <status> <duration>ms

Health-probe paths and CORS preflights (OPTIONS) are skipped so k8s liveness
/ readiness checks don't flood the log.
"""

import time

from loguru import logger
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_SKIP_PATH_PREFIXES = ("/health",)


class AccessLogMiddleware:
    """Log one line per HTTP request, safe for streaming responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        path = scope["path"]
        if method == "OPTIONS" or any(path.startswith(p) for p in _SKIP_PATH_PREFIXES):
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            client = scope.get("client")
            ip = client[0] if client else "-"
            query = scope.get("query_string", b"").decode("latin-1")
            full_path = f"{path}?{query}" if query else path
            logger.bind(access=True).info(
                f"{ip} {method} {full_path} {status['code']} {duration_ms:.1f}ms"
            )
