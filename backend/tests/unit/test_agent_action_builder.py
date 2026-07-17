"""Tests for the per-operation capability tool builder.

Each operation in an AgentCapability becomes a standalone AgentTool named
``<cap_name>_<op_name>``; the umbrella+discriminator pattern is gone.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

from pydantic import BaseModel

from cubeplex.agents.actions.builder import build_capability_tools
from cubeplex.agents.actions.context import ScopeContext
from cubeplex.agents.actions.types import (
    ActionInvalidInput,
    ActionNotFound,
    ActionPermissionDenied,
    AgentCapability,
    AgentOperation,
)
from cubeplex.models.membership import Role

FAKE_CTX = ScopeContext(
    org_id="org_1",
    workspace_id="ws_1",
    user_id="user_1",
    role=Role.MEMBER,
    conversation_id="conv_1",
)


@asynccontextmanager
async def fake_context_factory() -> AsyncGenerator[tuple[ScopeContext, Any]]:
    yield (FAKE_CTX, "fake-session")


class ListInput(BaseModel):
    page: int = 1


class CreateInput(BaseModel):
    title: str


class DeleteInput(BaseModel):
    item_id: str


class TestMutationGate:
    def test_allow_mutations_includes_all(self) -> None:
        list_op = AgentOperation(
            name="list",
            description="List items",
            input_model=ListInput,
            handler=AsyncMock(),
            mutates=False,
        )
        create_op = AgentOperation(
            name="create",
            description="Create item",
            input_model=CreateInput,
            handler=AsyncMock(),
            mutates=True,
        )
        cap = AgentCapability(
            name="items",
            description="Item management",
            operations=[list_op, create_op],
        )
        tools = build_capability_tools(cap, fake_context_factory, allow_mutations=True)
        assert [t.name for t in tools] == ["items_list", "items_create"]

    def test_deny_mutations_drops_mutating(self) -> None:
        list_op = AgentOperation(
            name="list",
            description="List items",
            input_model=ListInput,
            handler=AsyncMock(),
            mutates=False,
        )
        create_op = AgentOperation(
            name="create",
            description="Create item",
            input_model=CreateInput,
            handler=AsyncMock(),
            mutates=True,
        )
        cap = AgentCapability(
            name="items",
            description="Item management",
            operations=[list_op, create_op],
        )
        tools = build_capability_tools(cap, fake_context_factory, allow_mutations=False)
        assert [t.name for t in tools] == ["items_list"]

    def test_always_mutable_capability_bypasses_gate(self) -> None:
        """``always_mutable=True`` keeps mutating ops even when the run-level
        ``allow_mutations`` flag is off — used by scheduled_tasks so IM users
        and schedule fires can still create/cancel tasks."""
        list_op = AgentOperation(
            name="list",
            description="List items",
            input_model=ListInput,
            handler=AsyncMock(),
            mutates=False,
        )
        create_op = AgentOperation(
            name="create",
            description="Create item",
            input_model=CreateInput,
            handler=AsyncMock(),
            mutates=True,
        )
        cap = AgentCapability(
            name="items",
            description="Item management",
            operations=[list_op, create_op],
            always_mutable=True,
        )
        tools = build_capability_tools(cap, fake_context_factory, allow_mutations=False)
        assert [t.name for t in tools] == ["items_list", "items_create"]

    def test_deny_mutations_all_mutating_returns_empty(self) -> None:
        create_op = AgentOperation(
            name="create",
            description="Create item",
            input_model=CreateInput,
            handler=AsyncMock(),
            mutates=True,
        )
        delete_op = AgentOperation(
            name="delete",
            description="Delete item",
            input_model=DeleteInput,
            handler=AsyncMock(),
            mutates=True,
        )
        cap = AgentCapability(
            name="items",
            description="Item management",
            operations=[create_op, delete_op],
        )
        tools = build_capability_tools(cap, fake_context_factory, allow_mutations=False)
        assert tools == []


class TestSchema:
    def test_per_op_parameters_is_the_op_input_model(self) -> None:
        list_op = AgentOperation(
            name="list",
            description="List items",
            input_model=ListInput,
            handler=AsyncMock(),
            mutates=False,
        )
        create_op = AgentOperation(
            name="create",
            description="Create item",
            input_model=CreateInput,
            handler=AsyncMock(),
            mutates=True,
        )
        cap = AgentCapability(
            name="items",
            description="Item management",
            operations=[list_op, create_op],
        )
        tools = build_capability_tools(cap, fake_context_factory, allow_mutations=True)
        by_name = {t.name: t for t in tools}
        # The model sees the operation's own input model directly — no
        # discriminator wrapper, no Op_<name> sub-model.
        assert by_name["items_list"].parameters is ListInput
        assert by_name["items_create"].parameters is CreateInput

    def test_per_op_description_lands_on_the_tool(self) -> None:
        list_op = AgentOperation(
            name="list",
            description="List items. Example: {}",
            input_model=ListInput,
            handler=AsyncMock(),
            mutates=False,
        )
        cap = AgentCapability(
            name="items",
            description="Item management",
            operations=[list_op],
        )
        tools = build_capability_tools(cap, fake_context_factory, allow_mutations=True)
        assert tools[0].description == "List items. Example: {}"


class TestDispatch:
    async def test_call_routes_to_op_handler(self) -> None:
        handler = AsyncMock(return_value={"status": "ok"})
        op = AgentOperation(
            name="list",
            description="List items",
            input_model=ListInput,
            handler=handler,
            mutates=False,
        )
        cap = AgentCapability(name="items", description="Items", operations=[op])
        tools = build_capability_tools(cap, fake_context_factory, allow_mutations=True)

        args = ListInput(page=3)
        result = await tools[0].execute("call_1", args)

        assert result.is_error is None
        handler.assert_awaited_once_with(FAKE_CTX, "fake-session", args)

    async def test_handler_receives_ctx_session_input(self) -> None:
        handler = AsyncMock(return_value={"ok": True})
        op = AgentOperation(
            name="create",
            description="Create",
            input_model=CreateInput,
            handler=handler,
            mutates=True,
        )
        cap = AgentCapability(name="things", description="Things", operations=[op])
        tools = build_capability_tools(cap, fake_context_factory, allow_mutations=True)

        await tools[0].execute("call_3", CreateInput(title="Test"))

        ctx_arg, session_arg, input_arg = handler.call_args[0]
        assert ctx_arg is FAKE_CTX
        assert session_arg == "fake-session"
        assert isinstance(input_arg, CreateInput)
        assert input_arg.title == "Test"


class TestErrorMapping:
    async def test_action_not_found(self) -> None:
        handler = AsyncMock(side_effect=ActionNotFound("item xyz not found"))
        op = AgentOperation(
            name="get",
            description="Get item",
            input_model=ListInput,
            handler=handler,
        )
        cap = AgentCapability(name="items", description="Items", operations=[op])
        tools = build_capability_tools(cap, fake_context_factory, allow_mutations=True)

        result = await tools[0].execute("call_err1", ListInput())
        assert result.is_error is True
        text = result.content[0].text  # type: ignore[union-attr]
        assert "ActionNotFound" in text
        assert "item xyz not found" in text

    async def test_action_permission_denied(self) -> None:
        handler = AsyncMock(side_effect=ActionPermissionDenied("admin required"))
        op = AgentOperation(
            name="delete",
            description="Delete item",
            input_model=DeleteInput,
            handler=handler,
            mutates=True,
        )
        cap = AgentCapability(name="items", description="Items", operations=[op])
        tools = build_capability_tools(cap, fake_context_factory, allow_mutations=True)

        result = await tools[0].execute("call_err2", DeleteInput(item_id="x"))
        assert result.is_error is True
        text = result.content[0].text  # type: ignore[union-attr]
        assert "ActionPermissionDenied" in text
        assert "admin required" in text

    async def test_action_invalid_input(self) -> None:
        handler = AsyncMock(side_effect=ActionInvalidInput("bad cron expression"))
        op = AgentOperation(
            name="create",
            description="Create",
            input_model=CreateInput,
            handler=handler,
            mutates=True,
        )
        cap = AgentCapability(name="items", description="Items", operations=[op])
        tools = build_capability_tools(cap, fake_context_factory, allow_mutations=True)

        result = await tools[0].execute("call_err3", CreateInput(title="bad"))
        assert result.is_error is True
        text = result.content[0].text  # type: ignore[union-attr]
        assert "ActionInvalidInput" in text
        assert "bad cron expression" in text
