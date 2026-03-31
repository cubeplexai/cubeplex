"""Pure ASGI middleware to handle request cancellation gracefully.

Replaces BaseHTTPMiddleware to avoid task-boundary cancellation issues
that cause SQLAlchemy async connection pool leaks.
"""

import asyncio

from loguru import logger
from starlette.types import ASGIApp, Receive, Scope, Send


class CancellationMiddleware:
    """Handle request cancellation (client disconnect) gracefully."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            await self.app(scope, receive, send)
        except asyncio.CancelledError:
            # 客户端断开连接，记录 debug 级别日志，不污染错误日志
            logger.debug(
                "Request cancelled (client disconnect): {} {}",
                scope["method"],
                scope["path"],
            )
            raise
