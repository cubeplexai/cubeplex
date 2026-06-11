"""Build per-operation cubepi AgentTools from an AgentCapability declaration.

Each capability operation becomes its own AgentTool named ``<cap_name>_<op_name>``
(e.g. ``scheduled_tasks_create``). Tools from the same capability are then
grouped under a :class:`DeferredToolGroup` by the caller; the model only sees
the group catalog until it calls ``load_tools``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from cubepi.types import StructuredValue
from pydantic import BaseModel

from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import (
    ActionInvalidInput,
    ActionNotFound,
    ActionPermissionDenied,
    AgentCapability,
    AgentOperation,
)

logger = logging.getLogger(__name__)

ContextFactory = Callable[[], AbstractAsyncContextManager[tuple[ScopeContext, Any]]]


def _make_op_tool(
    cap_name: str,
    op: AgentOperation,
    context_factory: ContextFactory,
) -> AgentTool[Any]:
    """Wrap one AgentOperation as a standalone AgentTool."""

    full_name = f"{cap_name}_{op.name}"

    async def _execute(
        tool_call_id: str,
        args: BaseModel,
        *,
        signal: asyncio.Event | None = None,
        on_update: Callable[[StructuredValue], None] | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        try:
            async with context_factory() as (ctx, session):
                result = await op.handler(ctx, session, args)
        except (
            ActionNotFound,
            ActionPermissionDenied,
            ActionInvalidInput,
        ) as exc:
            return AgentToolResult(
                content=[TextContent(text=f"{type(exc).__name__}: {exc}")],
                is_error=True,
            )

        if isinstance(result, BaseModel):
            text = result.model_dump_json()
        else:
            text = json.dumps(result, default=str)

        return AgentToolResult(content=[TextContent(text=text)])

    return AgentTool(
        name=full_name,
        description=op.description,
        parameters=op.input_model,
        execute=_execute,
    )


def build_capability_tools(
    cap: AgentCapability,
    context_factory: ContextFactory,
    *,
    allow_mutations: bool,
) -> list[AgentTool[Any]]:
    """One AgentTool per operation that survives the mutation gate.

    Returns an empty list when no operations survive — callers should skip
    building a DeferredToolGroup in that case.
    """
    surviving = [op for op in cap.operations if allow_mutations or not op.mutates]
    return [_make_op_tool(cap.name, op, context_factory) for op in surviving]
