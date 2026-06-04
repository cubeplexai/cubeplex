"""Build a single cubepi AgentTool from an AgentCapability definition."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Annotated, Any, Literal, Union

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from cubepi.types import StructuredValue
from pydantic import BaseModel, Field, RootModel, create_model

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


def _make_literal_type(value: str) -> Any:
    """Create ``Literal["value"]`` at runtime (opaque to static analysis)."""
    # Literal subscript with a runtime string is valid at runtime but
    # cannot be tracked statically — the Any return communicates that.
    return Literal[value]  # noqa: F821 – runtime subscript


def _build_operation_model(op: AgentOperation) -> type[BaseModel]:
    """Create a wrapper model with a fixed ``operation`` discriminator field.

    The wrapper inherits all fields from the operation's ``input_model`` and
    prepends an ``operation: Literal["<name>"]`` discriminator.
    """
    op_literal = _make_literal_type(op.name)
    field_definitions: dict[str, Any] = {
        "operation": (op_literal, ...),
    }
    for field_name, field_info in op.input_model.model_fields.items():
        field_definitions[field_name] = (
            field_info.annotation,
            field_info,
        )
    model: Any = create_model(
        f"Op_{op.name}",
        **field_definitions,
    )
    # Surface the operation's description into the generated JSON Schema —
    # pydantic v2 reads __doc__ as the schema's "description" field, which
    # tool-calling LLMs (Anthropic / OpenAI) render alongside each oneOf
    # variant. Without this, per-op example payloads in AgentOperation
    # never reach the model.
    model.__doc__ = op.description
    return model  # type: ignore[no-any-return]


def _build_union_model(
    cap_name: str,
    operations: list[AgentOperation],
) -> type[BaseModel]:
    """Build a discriminated-union input model over multiple operations.

    Uses ``RootModel`` so the discriminated union IS the top-level schema
    (no wrapping ``params`` field). The LLM sees ``{operation: "create", ...}``
    directly.

    Pydantic's RootModel emits a top-level schema of ``{oneOf: [...]}`` with no
    ``type``. LLM tool APIs (Anthropic ``input_schema``, OpenAI ``parameters``)
    reject this with "schema must be type: object". Since every branch IS an
    object, we override ``model_json_schema`` to inject ``type: "object"``.
    """
    sub_models = [_build_operation_model(op) for op in operations]

    union_type: Any = Union[tuple(sub_models)]  # noqa: UP007
    annotated_union = Annotated[union_type, Field(discriminator="operation")]

    base_root = RootModel[annotated_union]

    def _patched_schema(cls: type[BaseModel], *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema: dict[str, Any] = base_root.model_json_schema(*args, **kwargs)
        if "type" not in schema:
            schema["type"] = "object"
        return schema

    model: Any = type(
        f"{cap_name}_Input",
        (base_root,),
        {"model_json_schema": classmethod(_patched_schema)},
    )
    return model  # type: ignore[no-any-return]


def build_capability_tool(
    cap: AgentCapability,
    context_factory: ContextFactory,
    *,
    allow_mutations: bool,
) -> AgentTool[Any] | None:
    """Convert an :class:`AgentCapability` into a cubepi :class:`AgentTool`.

    Returns ``None`` when no operations survive the mutation gate (e.g. all
    operations mutate and ``allow_mutations=False``).
    """
    surviving: list[AgentOperation] = [
        op for op in cap.operations if allow_mutations or not op.mutates
    ]
    if not surviving:
        return None

    # Build the handler lookup.
    ops_by_name: dict[str, AgentOperation] = {op.name: op for op in surviving}

    # Single-operation optimisation: expose the operation's own model
    # directly (no discriminated wrapper).
    single = len(surviving) == 1

    if single:
        the_op = surviving[0]
        params_model: type[BaseModel] = the_op.input_model
    else:
        params_model = _build_union_model(cap.name, surviving)

    async def _execute(
        tool_call_id: str,
        args: BaseModel,
        *,
        signal: asyncio.Event | None = None,
        on_update: Callable[[StructuredValue], None] | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        # Determine which operation was called.
        if single:
            op = surviving[0]
            parsed = args
        else:
            # RootModel: the discriminated payload is ``.root``.
            inner = args.root  # type: ignore[attr-defined]
            op_name: str = inner.operation
            op = ops_by_name[op_name]
            parsed = inner

        try:
            async with context_factory() as (ctx, session):
                result = await op.handler(ctx, session, parsed)
        except (
            ActionNotFound,
            ActionPermissionDenied,
            ActionInvalidInput,
        ) as exc:
            return AgentToolResult(
                content=[TextContent(text=f"{type(exc).__name__}: {exc}")],
                is_error=True,
            )

        # Serialize the handler result to JSON text.
        if isinstance(result, BaseModel):
            text = result.model_dump_json()
        else:
            text = json.dumps(result, default=str)

        return AgentToolResult(
            content=[TextContent(text=text)],
        )

    return AgentTool(
        name=cap.name,
        description=cap.description,
        parameters=params_model,
        execute=_execute,
    )
