"""Built-in memory tools — save, search, update."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from cubebox.models.memory import MemoryScope, MemoryStatus, MemoryType
from cubebox.services.memory import (
    CreateMemoryInput,
    MemoryPermissionError,
    MemoryService,
)
from cubebox.services.memory_screen import MemoryScreenError


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


def create_memory_tools(
    *,
    service_factory: Callable[[], AbstractAsyncContextManager[MemoryService]],
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> list[StructuredTool]:
    """Build the three memory tools bound to a request-scoped service factory.

    `service_factory()` yields an async context manager so the AsyncSession
    backing the service closes after each tool invocation.
    """

    async def memory_save(args: MemorySaveArgs) -> dict[str, Any]:
        async with service_factory() as svc:
            try:
                item = await svc.create(
                    CreateMemoryInput(
                        scope=args.scope,
                        type=args.type,
                        content=args.content,
                        confidence=args.confidence,
                        source_conversation_id=conversation_id,
                        source_run_id=run_id,
                    )
                )
            except MemoryPermissionError as exc:
                return {"status": "error", "error": str(exc)}
            except MemoryScreenError as exc:
                return {"status": "rejected", "error": str(exc)}
            return {"status": "saved", "memory_id": item.id}

    async def memory_search(args: MemorySearchArgs) -> dict[str, Any]:
        async with service_factory() as svc:
            items = await svc.repo.list(
                scope=args.scope,
                type_=args.type,
                q=args.query,
                limit=args.limit,
            )
            return {
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

    async def memory_update(args: MemoryUpdateArgs) -> dict[str, Any]:
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
                return {"status": "error", "error": str(exc)}
            except MemoryScreenError as exc:
                return {"status": "rejected", "error": str(exc)}
            return {"status": "updated", "memory_id": item.id}

    return [
        StructuredTool.from_function(
            coroutine=memory_save,
            name="memory_save",
            description=(
                "Save a durable knowledge item. scope=personal for the current "
                "user only; scope=workspace for all members of this workspace; "
                "scope=org for all members of this organization. Choose type "
                "carefully: preference (style/behavior), correction (fix a "
                "repeated mistake), procedure (a workflow), project_fact, "
                "decision, org_policy."
            ),
            args_schema=MemorySaveArgs,
        ),
        StructuredTool.from_function(
            coroutine=memory_search,
            name="memory_search",
            description=(
                "Search active memory for items relevant to a query. Use when "
                "you need details that the auto-injected memory block didn't "
                "include, or to confirm what's been saved this turn."
            ),
            args_schema=MemorySearchArgs,
        ),
        StructuredTool.from_function(
            coroutine=memory_update,
            name="memory_update",
            description=(
                "Edit or archive an existing memory item. Pass status='archived' "
                "to retire an item without deleting. Use this instead of "
                "memory_save when correcting an existing item — saving a new one "
                "creates contradictory memory."
            ),
            args_schema=MemoryUpdateArgs,
        ),
    ]
