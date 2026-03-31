"""Middleware to handle request cancellation gracefully."""

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware


class CancellationMiddleware(BaseHTTPMiddleware):
    """Handle request cancellation (client disconnect) gracefully."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process request and handle cancellation."""
        try:
            return await call_next(request)
        except asyncio.CancelledError:
            # 客户端断开连接，记录 debug 级别日志，不污染错误日志
            logger.debug(
                "Request cancelled (client disconnect): {} {}",
                request.method,
                request.url.path,
            )
            # 重新抛出，让 FastAPI 处理
            raise
