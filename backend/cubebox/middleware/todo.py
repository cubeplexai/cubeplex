"""Local todo middleware with stable JSON tool results."""

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, NotRequired, cast, override

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

WRITE_TODOS_TOOL_DESCRIPTION = """Use this tool to create and manage a structured task list for your current work session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.

Only use this tool if you think it will be helpful in staying organized. If the user's request is trivial and takes less than 3 steps, it is better to NOT use this tool and just do the task directly.

## When to Use This Tool
Use this tool in these scenarios:

1. Complex multi-step tasks - When a task requires 3 or more distinct steps or actions
2. Non-trivial and complex tasks - Tasks that require careful planning or multiple operations
3. User explicitly requests todo list - When the user directly asks you to use the todo list
4. User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)
5. The plan may need future revisions or updates based on results from the first few steps

## How to Use This Tool
1. When you start working on a task - Mark it as in_progress BEFORE beginning work.
2. After completing a task - Mark it as completed and add any new follow-up tasks discovered during implementation.
3. You can also update future tasks, such as deleting them if they are no longer necessary, or adding new tasks that are necessary. Don't change previously completed tasks.
4. You can make several updates to the todo list at once. For example, when you complete a task, you can mark the next task you need to start as in_progress.

## When NOT to Use This Tool
It is important to skip using this tool when:
1. There is only a single, straightforward task
2. The task is trivial and tracking it provides no benefit
3. The task can be completed in less than 3 trivial steps
4. The task is purely conversational or informational

## Task States and Management

1. **Task States**: Use these states to track progress:
   - pending: Task not yet started
   - in_progress: Currently working on (unless all tasks are completed, only one task should be in_progress)
   - completed: Task finished successfully

2. **Task Management**:
   - Update task status in real-time as you work
   - Mark tasks complete IMMEDIATELY after finishing (don't batch completions)
   - Complete current tasks before starting new ones
   - Remove tasks that are no longer relevant from the list entirely
   - IMPORTANT: When you write this todo list, you should mark your first task as in_progress immediately!.
   - IMPORTANT: Unless all tasks are completed, only one task should be in_progress.

3. **Task Completion Requirements**:
   - ONLY mark a task as completed when you have FULLY accomplished it
   - If you encounter errors, blockers, or cannot finish, keep the task as in_progress
   - When blocked, create a new task describing what needs to be resolved
   - Never mark a task as completed if:
     - There are unresolved issues or errors
     - Work is partial or incomplete
     - You encountered blockers that prevent completion
     - You couldn't find necessary resources or dependencies
     - Quality standards haven't been met

4. **Task Breakdown**:
   - Create specific, actionable items
   - Break complex tasks into smaller, manageable steps
   - Use clear, descriptive task names

Being proactive with task management demonstrates attentiveness and ensures you complete all requirements successfully
Remember: If you only need to make a few tool calls to complete a task, and it is clear what you need to do, it is better to just do the task directly and NOT call this tool at all."""  # noqa: E501

WRITE_TODOS_SYSTEM_PROMPT = """## `write_todos`

You have access to the `write_todos` tool to help you manage and plan complex objectives.
Use this tool for complex objectives to ensure that you are tracking each necessary step and giving the user visibility into your progress.
This tool is very helpful for planning complex objectives, and for breaking down these larger complex objectives into smaller steps.

It is critical that you mark todos as completed as soon as you are done with a step. Do not batch up multiple steps before marking them as completed.
For simple objectives that only require a few steps, it is better to just complete the objective directly and NOT use this tool.
Writing todos takes time and tokens, use it when it is helpful for managing complex many-step problems! But not for simple few-step requests.
- Unless all tasks are completed, only one task should be in_progress.

## Important To-Do List Usage Notes to Remember
- The `write_todos` tool should never be called multiple times in parallel.
- Don't be afraid to revise the To-Do list as you go. New information may reveal new tasks that need to be done, or old tasks that are irrelevant."""  # noqa: E501


class Todo(TypedDict):
    """A single todo item with content and status."""

    content: str
    status: Literal["pending", "in_progress", "completed"]


type TodoGuardType = Literal["finalization"]

# Number of consecutive tool-call iterations (without write_todos) before
# injecting a soft stale-todo reminder.  The model is free to ignore the
# reminder; it is never a hard block.
STALE_REMINDER_THRESHOLD = 5

# Minimum iterations between successive stale reminders so the model is not
# nagged on every single turn.
STALE_REMINDER_INTERVAL = 5


class TodoGuardBlocked(TypedDict):
    """A guard escalation payload carried across the forced end turn."""

    guard_type: TodoGuardType
    message: str


class PlanningState(AgentState[ResponseT]):
    """State schema for todo tracking."""

    todos: Annotated[NotRequired[list[Todo]], OmitFromInput]
    todo_guard_retries: Annotated[NotRequired[dict[TodoGuardType, int]], OmitFromInput]
    todo_guard_blocked: Annotated[NotRequired[TodoGuardBlocked | None], OmitFromInput]
    todo_guard_suppressed: Annotated[NotRequired[bool], OmitFromInput]
    todo_stale_iterations: Annotated[NotRequired[int], OmitFromInput]
    todo_finalization_correction: Annotated[NotRequired[bool], OmitFromInput]


class WriteTodosInput(BaseModel):
    """Input schema for the ``write_todos`` tool."""

    todos: list[Todo]


def _build_todo_tool_message(
    tool_call_id: str | None,
    todos: list[Todo],
) -> ToolMessage:
    payload: dict[str, Any] = {"todos": todos}
    if len(todos) >= 3 and all(todo["status"] == "completed" for todo in todos):
        payload["reminder"] = (
            "All todo items are complete. Do a quick final check before responding."
        )

    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id=tool_call_id,
    )


def _build_todo_error_message(tool_call_id: str | None, error: str) -> ToolMessage:
    return ToolMessage(content=error, tool_call_id=tool_call_id, status="error")


def _write_todos_payload_error(todos: list[Todo]) -> str | None:
    if any(not todo["content"].strip() for todo in todos):
        return "Error: Todo content cannot be empty."

    in_progress_count = sum(1 for todo in todos if todo["status"] == "in_progress")
    if in_progress_count == 0 and any(todo["status"] != "completed" for todo in todos):
        return "Error: Unless all tasks are completed, exactly one todo must be in_progress."
    if in_progress_count > 1:
        return "Error: Unless all tasks are completed, exactly one todo must be in_progress."

    return None


def _write_todos_empty_payload_error(
    todos: list[Todo],
    prior_todos: list[Todo] | None,
) -> str | None:
    if todos:
        return None
    if any(todo["status"] != "completed" for todo in (prior_todos or [])):
        return (
            "Error: Cannot replace unfinished todos with an empty list. "
            "Update the active items first."
        )
    return None


def _last_ai_message(messages: list[Any]) -> AIMessage | None:
    return next((msg for msg in reversed(messages) if isinstance(msg, AIMessage)), None)


def _message_has_text_content(content: Any) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False

    for block in content:
        if isinstance(block, str) and block.strip():
            return True
        if (
            isinstance(block, dict)
            and block.get("type") == "text"
            and str(block.get("text", "")).strip()
        ):
            return True

    return False


def _pure_text_ai_response(last_ai_msg: AIMessage) -> bool:
    return _message_has_text_content(last_ai_msg.content) and not last_ai_msg.tool_calls


def _unfinished_todos(todos: list[Todo] | None) -> list[Todo]:
    return [todo for todo in (todos or []) if todo["status"] != "completed"]


def _submitted_write_todos_calls(last_ai_msg: AIMessage) -> list[dict[str, Any]]:
    return cast(
        "list[dict[str, Any]]",
        [tc for tc in (last_ai_msg.tool_calls or []) if tc["name"] == "write_todos"],
    )


def _non_todo_tool_calls(last_ai_msg: AIMessage) -> list[dict[str, Any]]:
    return cast(
        "list[dict[str, Any]]",
        [tc for tc in (last_ai_msg.tool_calls or []) if tc["name"] != "write_todos"],
    )


def _guard_retry_update(
    state: PlanningState[ResponseT],
    guard_type: TodoGuardType,
) -> tuple[int, dict[TodoGuardType, int]]:
    retries = dict(state.get("todo_guard_retries", {}))
    retries[guard_type] = retries.get(guard_type, 0) + 1
    return retries[guard_type], retries


_STALE_REMINDER_TEXT = (
    "The todo list has not been updated for several iterations. "
    "If you have completed the current task or started a new one, "
    "consider calling write_todos to keep the checklist in sync. "
    "Ignore this if the current work is still part of the active task."
)


def _guard_error_message(guard_type: TodoGuardType) -> str:
    return (
        "The todo list still has unfinished items. Call write_todos to update the "
        "remaining items. Your detailed response was already delivered to the user — "
        "after updating the list, give only a brief one-sentence closing. "
        "Do not repeat or re-summarize your earlier response."
    )


def _reset_guard_retries() -> dict[TodoGuardType, int]:
    return {}


def _blocked_todo_guard_message(blocked: TodoGuardBlocked) -> str:
    return (
        "Todo synchronization is already blocked. Do not call any tools. "
        "Respond to the user with a plain-text explanation that the run could "
        f"not continue safely because: {blocked['message']}"
    )


def _validated_write_todos_payload(
    tool_call: dict[str, Any],
) -> tuple[list[Todo] | None, str | None]:
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return (
            None,
            "Error: Received invalid `write_todos` payload. "
            "Call the tool again with a `todos` list.",
        )

    todos = args.get("todos")
    if not isinstance(todos, list):
        return (
            None,
            "Error: Received invalid `write_todos` payload. "
            "Call the tool again with a `todos` list.",
        )

    for todo in todos:
        if not isinstance(todo, dict):
            return None, (
                "Error: Received invalid `write_todos` payload. Each todo must include "
                "`content` and `status` fields."
            )
        if not isinstance(todo.get("content"), str) or not isinstance(todo.get("status"), str):
            return None, (
                "Error: Received invalid `write_todos` payload. Each todo must include "
                "`content` and `status` fields."
            )
        if todo["status"] not in {"pending", "in_progress", "completed"}:
            return None, (
                "Error: Received invalid `write_todos` payload. Todo status must be one of "
                "`pending`, `in_progress`, or `completed`."
            )

    return cast("list[Todo]", todos), None


def _todo_validation_errors(
    last_ai_msg: AIMessage,
    prior_todos: list[Todo] | None,
) -> list[ToolMessage]:
    write_todos_calls = _submitted_write_todos_calls(last_ai_msg)
    # Zero calls means there is nothing to validate. More than one call is handled
    # by the upstream parallel-write_todos check in _after_model_impl().
    if len(write_todos_calls) != 1:
        return []

    tool_call = write_todos_calls[0]
    todos, payload_error = _validated_write_todos_payload(tool_call)
    if payload_error is not None:
        return [_build_todo_error_message(tool_call.get("id"), payload_error)]
    assert todos is not None

    empty_payload_error = _write_todos_empty_payload_error(todos, prior_todos)
    if empty_payload_error is not None:
        return [_build_todo_error_message(tool_call["id"], empty_payload_error)]

    error = _write_todos_payload_error(todos)
    if error is None:
        return []

    return [_build_todo_error_message(tool_call["id"], error)]


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

    def _guard_response(
        self,
        state: PlanningState[ResponseT],
        guard_type: TodoGuardType,
    ) -> dict[str, Any]:
        retry_count, retries = _guard_retry_update(state, guard_type)
        message = _guard_error_message(guard_type)

        if retry_count >= 3:
            return {
                "jump_to": "model",
                "todo_guard_retries": retries,
                "todo_guard_blocked": {"guard_type": guard_type, "message": message},
                "messages": [
                    SystemMessage(
                        content=(
                            "Todo synchronization failed repeatedly. Do not call any tools. "
                            "Respond to the user with a plain-text explanation that the run "
                            f"could not continue safely because: {message}"
                        )
                    )
                ],
            }

        return {
            "jump_to": "model",
            "todo_guard_retries": retries,
            "todo_finalization_correction": True,
            "messages": [SystemMessage(content=message)],
        }

    def _parallel_write_todos_error(
        self,
        state: PlanningState[ResponseT],
    ) -> dict[str, Any] | None:
        messages = state["messages"]
        last_ai_msg = _last_ai_message(messages)
        if last_ai_msg is None:
            return None

        write_todos_calls = _submitted_write_todos_calls(last_ai_msg)
        if len(write_todos_calls) <= 1:
            return None

        return {
            "messages": [
                _build_todo_error_message(
                    tc["id"],
                    (
                        "Error: The `write_todos` tool should never be called multiple times "
                        "in parallel. Please call it only once per model invocation to update "
                        "the todo list."
                    ),
                )
                for tc in write_todos_calls
            ]
        }

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

    def _after_model_impl(
        self,
        state: PlanningState[ResponseT],
    ) -> dict[str, Any] | None:
        messages = state["messages"]
        if not messages:
            return None

        last_ai_msg = _last_ai_message(messages)
        if last_ai_msg is None:
            return None

        # --- blocked-guard state machine (finalization escalation) -----------
        blocked_guard = state.get("todo_guard_blocked")
        if blocked_guard:
            if _pure_text_ai_response(last_ai_msg):
                return {
                    "jump_to": "end",
                    "todo_guard_blocked": None,
                    "todo_guard_retries": _reset_guard_retries(),
                    "todo_guard_suppressed": True,
                    "todo_stale_iterations": 0,
                    "todo_finalization_correction": None,
                }

            return {
                "jump_to": "model",
                "todo_guard_blocked": blocked_guard,
                "todo_guard_retries": dict(state.get("todo_guard_retries", {})),
                "todo_finalization_correction": None,
                "messages": [SystemMessage(content=_blocked_todo_guard_message(blocked_guard))],
            }

        # --- suppression after escalation ------------------------------------
        if state.get("todo_guard_suppressed") and not _submitted_write_todos_calls(last_ai_msg):
            return {
                "todo_guard_retries": _reset_guard_retries(),
                "todo_guard_suppressed": True,
                "todo_stale_iterations": 0,
                "todo_finalization_correction": None,
            }

        # --- payload validation (parallel calls, schema, invariants) ---------
        parallel_error = self._parallel_write_todos_error(state)
        if parallel_error is not None:
            return parallel_error

        validation_errors = _todo_validation_errors(last_ai_msg, state.get("todos"))
        if validation_errors:
            return {"messages": validation_errors}

        # --- stale-todo soft reminder ----------------------------------------
        unfinished = _unfinished_todos(state.get("todos"))
        has_write_todos = bool(_submitted_write_todos_calls(last_ai_msg))
        has_non_todo_tools = bool(_non_todo_tool_calls(last_ai_msg))

        result: dict[str, Any] = {"todo_guard_retries": _reset_guard_retries()}
        if state.get("todo_guard_suppressed"):
            result["todo_guard_suppressed"] = None

        if unfinished and has_non_todo_tools and not has_write_todos:
            stale_count = state.get("todo_stale_iterations", 0) + 1
            result["todo_stale_iterations"] = stale_count
            if stale_count >= STALE_REMINDER_THRESHOLD and (
                (stale_count - STALE_REMINDER_THRESHOLD) % STALE_REMINDER_INTERVAL == 0
            ):
                result["messages"] = [SystemMessage(content=_STALE_REMINDER_TEXT)]
        else:
            # Any write_todos call or non-tool turn resets the counter.
            if has_write_todos or not has_non_todo_tools:
                result["todo_stale_iterations"] = 0

        # --- post-finalization-correction ------------------------------------
        # After the finalization guard fired and the model called write_todos,
        # the correction flag stays alive until the tool phase completes.
        # Once all todos are done the flag is cleared and the model's brief
        # closing response passes through normally (the guard message already
        # instructed it to keep the closing short and not repeat the earlier
        # detailed response).
        if state.get("todo_finalization_correction"):
            if not unfinished or not has_write_todos:
                result["todo_finalization_correction"] = None

        # --- finalization hard guard -----------------------------------------
        if unfinished and _pure_text_ai_response(last_ai_msg):
            return self._guard_response(state, "finalization")

        return result

    @override
    def after_model(
        self,
        state: PlanningState[ResponseT],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        del runtime
        return self._after_model_impl(state)

    @override
    async def aafter_model(
        self,
        state: PlanningState[ResponseT],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        del runtime
        return self._after_model_impl(state)


TodoListMiddleware.after_model.__can_jump_to__ = ["model", "end"]  # type: ignore[attr-defined]
TodoListMiddleware.aafter_model.__can_jump_to__ = [  # type: ignore[attr-defined]
    "model",
    "end",
]
