"""Local todo middleware with stable JSON tool results."""

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, NotRequired, cast, override

from langchain.agents.middleware.todo import (
    WRITE_TODOS_SYSTEM_PROMPT,
    WRITE_TODOS_TOOL_DESCRIPTION,
)
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    OmitFromInput,
    ResponseT,
)
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel
from typing_extensions import TypedDict


class Todo(TypedDict):
    """A single todo item with content and status."""

    content: str
    status: Literal["pending", "in_progress", "completed"]


class PlanningState(AgentState[ResponseT]):
    """State schema for todo tracking."""

    todos: Annotated[NotRequired[list[Todo]], OmitFromInput]


class WriteTodosInput(BaseModel):
    """Input schema for the ``write_todos`` tool."""

    todos: list[Todo]


def _build_todo_tool_message(
    tool_call_id: str,
    todos: list[Todo],
) -> ToolMessage:
    return ToolMessage(
        content=json.dumps({"todos": todos}, ensure_ascii=False),
        tool_call_id=tool_call_id,
    )


def _write_todos(
    runtime: ToolRuntime[ContextT, PlanningState[ResponseT]],
    todos: list[Todo],
) -> Command[Any]:
    """Create and replace the current todo list."""
    return Command(
        update={
            "todos": todos,
            "messages": [_build_todo_tool_message(runtime.tool_call_id, todos)],
        }
    )


async def _awrite_todos(
    runtime: ToolRuntime[ContextT, PlanningState[ResponseT]],
    todos: list[Todo],
) -> Command[Any]:
    """Async variant of ``_write_todos``."""
    return _write_todos(runtime, todos)


class TodoListMiddleware(AgentMiddleware[PlanningState[ResponseT], ContextT, ResponseT]):
    """Todo middleware with JSON-encoded tool results."""

    state_schema = PlanningState  # type: ignore[assignment]

    def __init__(
        self,
        *,
        system_prompt: str = WRITE_TODOS_SYSTEM_PROMPT,
        tool_description: str = WRITE_TODOS_TOOL_DESCRIPTION,
    ) -> None:
        super().__init__()
        self.system_prompt = system_prompt
        self.tool_description = tool_description
        self.tools = [
            StructuredTool.from_function(
                name="write_todos",
                description=tool_description,
                func=_write_todos,
                coroutine=_awrite_todos,
                args_schema=WriteTodosInput,
                infer_schema=False,
            )
        ]

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT] | AIMessage:
        if request.system_message is not None:
            new_system_content = [
                *request.system_message.content_blocks,
                {"type": "text", "text": f"\n\n{self.system_prompt}"},
            ]
        else:
            new_system_content = [{"type": "text", "text": self.system_prompt}]
        new_system_message = SystemMessage(
            content=cast("list[str | dict[str, str]]", new_system_content)
        )
        return handler(request.override(system_message=new_system_message))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT] | AIMessage:
        if request.system_message is not None:
            new_system_content = [
                *request.system_message.content_blocks,
                {"type": "text", "text": f"\n\n{self.system_prompt}"},
            ]
        else:
            new_system_content = [{"type": "text", "text": self.system_prompt}]
        new_system_message = SystemMessage(
            content=cast("list[str | dict[str, str]]", new_system_content)
        )
        return await handler(request.override(system_message=new_system_message))

    def _parallel_write_todos_error(
        self,
        state: PlanningState[ResponseT],
    ) -> dict[str, Any] | None:
        messages = state["messages"]
        if not messages:
            return None

        last_ai_msg = next((msg for msg in reversed(messages) if isinstance(msg, AIMessage)), None)
        if not last_ai_msg or not last_ai_msg.tool_calls:
            return None

        write_todos_calls = [tc for tc in last_ai_msg.tool_calls if tc["name"] == "write_todos"]
        if len(write_todos_calls) <= 1:
            return None

        return {
            "messages": [
                ToolMessage(
                    content=(
                        "Error: The `write_todos` tool should never be called multiple times "
                        "in parallel. Please call it only once per model invocation to update "
                        "the todo list."
                    ),
                    tool_call_id=tc["id"],
                    status="error",
                )
                for tc in write_todos_calls
            ]
        }

    @override
    def after_model(
        self,
        state: PlanningState[ResponseT],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        del runtime
        return self._parallel_write_todos_error(state)

    @override
    async def aafter_model(
        self,
        state: PlanningState[ResponseT],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        del runtime
        return self._parallel_write_todos_error(state)
