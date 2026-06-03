"""Tests for the generic capability tool builder."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

from pydantic import BaseModel

from cubebox.agents.actions.builder import build_capability_tool
from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import (
    ActionInvalidInput,
    ActionNotFound,
    ActionPermissionDenied,
    AgentCapability,
    AgentOperation,
)
from cubebox.models.membership import Role

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Mutation gate
# ---------------------------------------------------------------------------


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
        tool = build_capability_tool(cap, fake_context_factory, allow_mutations=True)
        assert tool is not None
        # The union model should contain both operations.
        schema = tool.parameters.model_json_schema()
        # Both sub-models should appear under $defs.
        assert "Op_list" in str(schema)
        assert "Op_create" in str(schema)

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
        tool = build_capability_tool(cap, fake_context_factory, allow_mutations=False)
        assert tool is not None
        # Single surviving op → no union wrapper, model is ListInput.
        assert tool.parameters is ListInput

    def test_deny_mutations_all_mutating_returns_none(self) -> None:
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
        result = build_capability_tool(cap, fake_context_factory, allow_mutations=False)
        assert result is None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    async def test_single_op_dispatches_correctly(self) -> None:
        handler = AsyncMock(return_value={"status": "ok"})
        list_op = AgentOperation(
            name="list",
            description="List items",
            input_model=ListInput,
            handler=handler,
            mutates=False,
        )
        cap = AgentCapability(
            name="items",
            description="Item management",
            operations=[list_op],
        )
        tool = build_capability_tool(cap, fake_context_factory, allow_mutations=True)
        assert tool is not None

        # Build input matching the single-op model (ListInput directly).
        args = ListInput(page=3)
        result = await tool.execute("call_1", args)

        assert result.is_error is None
        handler.assert_awaited_once_with(FAKE_CTX, "fake-session", args)

    async def test_multi_op_dispatches_to_correct_handler(self) -> None:
        list_handler = AsyncMock(return_value={"items": []})
        create_handler = AsyncMock(return_value={"id": "new_1"})
        list_op = AgentOperation(
            name="list",
            description="List items",
            input_model=ListInput,
            handler=list_handler,
            mutates=False,
        )
        create_op = AgentOperation(
            name="create",
            description="Create item",
            input_model=CreateInput,
            handler=create_handler,
            mutates=True,
        )
        cap = AgentCapability(
            name="items",
            description="Item management",
            operations=[list_op, create_op],
        )
        tool = build_capability_tool(cap, fake_context_factory, allow_mutations=True)
        assert tool is not None

        # Call the "create" operation via the union model.
        args = tool.parameters.model_validate({"operation": "create", "title": "My Item"})
        result = await tool.execute("call_2", args)

        assert result.is_error is None
        create_handler.assert_awaited_once()
        # Verify the handler received (ctx, session, parsed_input).
        call_args = create_handler.call_args
        assert call_args[0][0] is FAKE_CTX
        assert call_args[0][1] == "fake-session"
        inner = call_args[0][2]
        assert inner.title == "My Item"

    async def test_handler_receives_correct_arguments(self) -> None:
        """Verify the handler receives (ctx, session, parsed_input)."""
        handler = AsyncMock(return_value={"ok": True})
        op = AgentOperation(
            name="create",
            description="Create",
            input_model=CreateInput,
            handler=handler,
            mutates=True,
        )
        cap = AgentCapability(
            name="things",
            description="Things",
            operations=[op],
        )
        tool = build_capability_tool(cap, fake_context_factory, allow_mutations=True)
        assert tool is not None

        args = CreateInput(title="Test")
        await tool.execute("call_3", args)

        ctx_arg, session_arg, input_arg = handler.call_args[0]
        assert ctx_arg is FAKE_CTX
        assert session_arg == "fake-session"
        assert isinstance(input_arg, CreateInput)
        assert input_arg.title == "Test"


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    async def test_action_not_found(self) -> None:
        handler = AsyncMock(side_effect=ActionNotFound("item xyz not found"))
        op = AgentOperation(
            name="get",
            description="Get item",
            input_model=ListInput,
            handler=handler,
        )
        cap = AgentCapability(
            name="items",
            description="Items",
            operations=[op],
        )
        tool = build_capability_tool(cap, fake_context_factory, allow_mutations=True)
        assert tool is not None

        result = await tool.execute("call_err1", ListInput())
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
        cap = AgentCapability(
            name="items",
            description="Items",
            operations=[op],
        )
        tool = build_capability_tool(cap, fake_context_factory, allow_mutations=True)
        assert tool is not None

        result = await tool.execute("call_err2", DeleteInput(item_id="x"))
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
        cap = AgentCapability(
            name="items",
            description="Items",
            operations=[op],
        )
        tool = build_capability_tool(cap, fake_context_factory, allow_mutations=True)
        assert tool is not None

        result = await tool.execute("call_err3", CreateInput(title="bad"))
        assert result.is_error is True
        text = result.content[0].text  # type: ignore[union-attr]
        assert "ActionInvalidInput" in text
        assert "bad cron expression" in text


class TestSchemaDescriptions:
    """Each operation's description must reach the generated JSON Schema.

    The LLM only sees `tool.description` (capability-level) plus
    `tool.parameters` (JSON Schema). If `AgentOperation.description` is not
    serialized into a per-variant `description` field on the Op_* sub-model,
    the per-op example payloads we author never reach the model.
    """

    def test_each_op_description_lands_in_schema(self) -> None:
        list_op = AgentOperation(
            name="list",
            description="List items. Example: {'operation':'list'}",
            input_model=ListInput,
            handler=AsyncMock(),
            mutates=False,
        )
        create_op = AgentOperation(
            name="create",
            description="Create item. Example: {'operation':'create','title':'x'}",
            input_model=CreateInput,
            handler=AsyncMock(),
            mutates=True,
        )
        cap = AgentCapability(
            name="items",
            description="Item management",
            operations=[list_op, create_op],
        )
        tool = build_capability_tool(cap, fake_context_factory, allow_mutations=True)
        assert tool is not None
        schema = tool.parameters.model_json_schema()

        op_list = schema["$defs"]["Op_list"]
        op_create = schema["$defs"]["Op_create"]
        assert op_list.get("description") == list_op.description
        assert op_create.get("description") == create_op.description
