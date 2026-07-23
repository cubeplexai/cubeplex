"""Eager ``sandbox_config`` tool — network policy + env inventory (no secrets)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from cubepi.types import StructuredValue
from pydantic import BaseModel

from cubeplex.services.sandbox_runtime_config import load_agent_view

logger = logging.getLogger(__name__)

# Loader opens its own session and returns the agent view dict.
SandboxConfigLoader = Callable[[], Awaitable[dict[str, Any]]]

_SANITIZED_LOADER_ERROR = "sandbox configuration is temporarily unavailable."


class _SandboxConfigArgs(BaseModel):
    """No required args in v1."""


def create_sandbox_config_tool(
    loader: SandboxConfigLoader,
) -> AgentTool[_SandboxConfigArgs]:
    """Build the diagnosis tool; ``loader`` must not use CredentialService."""

    async def _execute(
        tool_call_id: str,
        args: _SandboxConfigArgs,
        *,
        signal: asyncio.Event | None = None,
        on_update: Callable[[StructuredValue], None] | None = None,
    ) -> AgentToolResult:
        del tool_call_id, args, signal, on_update
        try:
            view = await loader()
        except Exception:
            # Do not leak driver/DB details into the model transcript.
            logger.exception("sandbox_config loader failed")
            return AgentToolResult(
                content=[TextContent(text=_SANITIZED_LOADER_ERROR)],
                is_error=True,
            )
        text = json.dumps(view, sort_keys=True, default=str)
        return AgentToolResult(content=[TextContent(text=text)])

    return AgentTool(
        name="sandbox_config",
        description=(
            "Inspect sandbox network policy and configured env inventory "
            "(names, kinds, scopes, secret host/header constraints — never values). "
            "Call on network failures, auth/401 errors, or missing-env diagnosis. "
            "Do not invent credentials; never print secret values. "
            "Prefer this tool over printenv for diagnosis. "
            "Network rules apply at sandbox create; recreate the sandbox after "
            "admin policy changes."
        ),
        parameters=_SandboxConfigArgs,
        execute=_execute,
    )


def make_session_loader(
    *,
    session_factory: Callable[[], Any],
    org_id: str,
    workspace_id: str,
    user_id: str,
    default_image: str,
) -> SandboxConfigLoader:
    """Build a loader that opens a fresh session per call via ``session_factory``.

    ``session_factory`` must return an async context manager yielding AsyncSession
    (same shape as ``async_session_maker()``).
    """

    async def _load() -> dict[str, Any]:
        async with session_factory() as session:
            return await load_agent_view(
                session,
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=user_id,
                default_image=default_image,
            )

    return _load
