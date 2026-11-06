"""Drop user-stop markers before the model sees the transcript."""

from __future__ import annotations

import asyncio

from cubeloop.agent.types import AgentContext
from cubeloop.middleware.base import Middleware
from cubeloop.providers.base import Message

from cubeplex.agents.user_stop import omit_user_stop_markers


class UserStopMiddleware(Middleware):
    """Omit ``kind=user_stop`` assistants from model context.

    Registered first so later ``transform_context`` hooks, including the
    todo suffix, never see the marker.
    """

    async def transform_context(
        self,
        messages: list[Message],
        *,
        ctx: AgentContext,
        signal: asyncio.Event | None = None,
    ) -> list[Message]:
        del ctx, signal
        return omit_user_stop_markers(messages)
