"""Memory CRUD tools as cubepi.AgentTool instances.

Factory: ``create_memory_tools(service_factory, ...)`` returns three
``cubepi.AgentTool`` instances (save / search / update). MemoryMiddleware
identifies them by name, so the tool names and schemas are part of the
public contract.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubeplex.models.memory import MemoryScope, MemorySourceType, MemoryStatus, MemoryType
from cubeplex.services.memory import (
    CreateMemoryInput,
    MemoryPermissionError,
    MemoryService,
)
from cubeplex.services.memory_screen import MemoryScreenError
from cubeplex.services.reflection_context import reflection_source_active

# ---------------------------------------------------------------------------
# Input schemas — mirrored verbatim from memory.py
# ---------------------------------------------------------------------------


class MemorySaveArgs(BaseModel):
    scope: MemoryScope
    type: MemoryType
    content: str = Field(min_length=1, max_length=5000)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=500)


class MemorySearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    scope: MemoryScope | None = None
    type: MemoryType | None = None
    limit: int = Field(default=10, ge=1, le=50)


class MemoryUpdateArgs(BaseModel):
    memory_id: str
    content: str | None = Field(default=None, max_length=5000)
    type: MemoryType | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: MemoryStatus | None = None
    reason: str = Field(default="", max_length=500)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_memory_tools(
    *,
    service_factory: Callable[[], AbstractAsyncContextManager[MemoryService]],
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> list[AgentTool]:  # type: ignore[type-arg]
    """Build the three memory cubepi.AgentTool instances backed by a service factory.

    Mirrors cubeplex.tools.builtin.memory.create_memory_tools — same tool names,
    same schemas, same business logic.  Only the wrapper shape changes: each
    tool's execute accepts (tool_call_id, args, *, signal, on_update).
    """

    async def _memory_save_execute(
        tool_call_id: str,
        args: MemorySaveArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        src_type = (
            MemorySourceType.REFLECTION
            if reflection_source_active()
            else MemorySourceType.CONVERSATION
        )
        async with service_factory() as svc:
            try:
                item = await svc.create(
                    CreateMemoryInput(
                        scope=args.scope,
                        type=args.type,
                        content=args.content,
                        confidence=args.confidence,
                        source_type=src_type,
                        source_conversation_id=conversation_id,
                        source_run_id=run_id,
                    )
                )
            except MemoryPermissionError as exc:
                result: dict[str, Any] = {"status": "error", "error": str(exc)}
            except MemoryScreenError as exc:
                result = {"status": "rejected", "error": str(exc)}
            else:
                result = {"status": "saved", "memory_id": item.id}
        return AgentToolResult(content=[TextContent(text=json.dumps(result))])

    async def _memory_search_execute(
        tool_call_id: str,
        args: MemorySearchArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        async with service_factory() as svc:
            items = await svc.repo.list(
                scope=args.scope,
                type_=args.type,
                q=args.query,
                limit=args.limit,
            )
            result = {
                "items": [
                    {
                        "id": i.id,
                        "scope": i.scope.value,
                        "type": i.type.value,
                        "content": i.content,
                        "confidence": i.confidence,
                    }
                    for i in items
                ]
            }
        return AgentToolResult(content=[TextContent(text=json.dumps(result))])

    async def _memory_update_execute(
        tool_call_id: str,
        args: MemoryUpdateArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        async with service_factory() as svc:
            try:
                item = await svc.update(
                    args.memory_id,
                    content=args.content,
                    type_=args.type,
                    confidence=args.confidence,
                    status=args.status,
                )
            except LookupError as exc:
                result = {"status": "error", "error": str(exc)}
            except MemoryScreenError as exc:
                result = {"status": "rejected", "error": str(exc)}
            else:
                result = {"status": "updated", "memory_id": item.id}
        return AgentToolResult(content=[TextContent(text=json.dumps(result))])

    memory_save = AgentTool(
        name="memory_save",
        description=(
            "Save a durable knowledge item. scope=personal for the current "
            "user only; scope=workspace for all members of this workspace; "
            "scope=org for all members of this organization. Choose type "
            "carefully: preference (style/behavior), correction (fix a "
            "repeated mistake), procedure (a workflow), project_fact, "
            "decision, org_policy."
        ),
        parameters=MemorySaveArgs,
        execute=_memory_save_execute,
    )

    memory_search = AgentTool(
        name="memory_search",
        description=(
            "Search active memory for items relevant to a query. Use when "
            "you need details that the auto-injected memory block didn't "
            "include, or to confirm what's been saved this turn."
        ),
        parameters=MemorySearchArgs,
        execute=_memory_search_execute,
    )

    memory_update = AgentTool(
        name="memory_update",
        description=(
            "Edit or archive an existing memory item. Pass status='archived' "
            "to retire an item without deleting. Use this instead of "
            "memory_save when correcting an existing item — saving a new one "
            "creates contradictory memory."
        ),
        parameters=MemoryUpdateArgs,
        execute=_memory_update_execute,
    )

    return [memory_save, memory_search, memory_update]
