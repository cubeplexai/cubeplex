"""Record completed agent tool calls without changing their results."""

from __future__ import annotations

import asyncio

from cubeloop.agent.types import AfterToolCallContext, AfterToolCallResult
from cubeloop.middleware.base import Middleware

from cubeplex.metrics import record_tool_call


class ToolMetricsMiddleware(Middleware):
    async def after_tool_call(
        self,
        ctx: AfterToolCallContext,
        *,
        signal: asyncio.Event | None = None,
    ) -> AfterToolCallResult | None:
        del signal
        record_tool_call(ctx.tool_call.name, is_error=ctx.is_error)
        return None
