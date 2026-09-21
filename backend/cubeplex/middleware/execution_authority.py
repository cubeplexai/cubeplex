"""Recheck durable execution authority before model and tool side effects."""

import asyncio
from collections.abc import Awaitable, Callable

from cubeloop.agent.types import AgentContext, BeforeToolCallContext
from cubeloop.middleware.base import Middleware
from cubeloop.providers.base import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.services.conversation_execution import (
    ConversationExecutionService,
    ExecutionRevokedError,
    RunExecutionBinding,
)


class ExecutionAuthorityMiddleware(Middleware):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        org_id: str,
        workspace_id: str,
        binding: RunExecutionBinding,
        require_slot: Callable[[], Awaitable[None]],
    ) -> None:
        self._sessions = session_factory
        self._org_id = org_id
        self._workspace_id = workspace_id
        self._binding = binding
        self._require_slot = require_slot

    async def _require_authority(self) -> None:
        await self._require_slot()
        try:
            async with self._sessions() as session:
                await ConversationExecutionService(
                    session, org_id=self._org_id, workspace_id=self._workspace_id
                ).require_run_authority(
                    admission_id=self._binding.admission_id,
                    attempt_id=self._binding.start_token,
                )
        except (ExecutionRevokedError, LookupError) as exc:
            raise asyncio.CancelledError("execution authority was revoked") from exc

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
