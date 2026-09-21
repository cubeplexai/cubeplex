"""Recheck durable execution authority before model and tool side effects."""

import asyncio
from collections.abc import Awaitable, Callable

from cubeloop.agent.types import AgentContext, BeforeToolCallContext
from cubeloop.middleware.base import Middleware
from cubeloop.providers.base import AssistantMessage, Message


class ExecutionAuthorityMiddleware(Middleware):
    def __init__(
        self,
        *,
        require_authority: Callable[[], Awaitable[None]],
    ) -> None:
        self._require_authority = require_authority

    async def transform_context(
        self,
        messages: list[Message],
        *,
        ctx: AgentContext,
        signal: asyncio.Event | None = None,
    ) -> list[Message]:
        await self._require_authority()
        return messages

    async def before_tool_call(
        self,
        ctx: BeforeToolCallContext,
        *,
        signal: asyncio.Event | None = None,
    ) -> None:
        await self._require_authority()

    async def after_model_response(
        self,
        response: AssistantMessage,
        ctx: AgentContext,
        *,
        signal: asyncio.Event | None = None,
    ) -> None:
        await self._require_authority()
