"""Unit tests for TodoListMiddleware (M3.e).

Covers ALL guard branches and hook behaviors:
- Tool injection (write_todos in mw.tools)
- transform_system_prompt appends write_todos system prompt
- transform_context renders current todos as UserMessage
- transform_context is pass-through when no todos
- after_tool_call is always None (write_todos tool handles its own extra writes)
- write_todos tool: successful write → extra["todos"] updated + JSON content
- write_todos tool: validation failures → error result
- after_model_response:
  - returns None when state is clean with tool calls
  - returns None when state is clean with no tool calls and no todos
  - blocked guard + pure-text → TurnAction(stop), resets all state
  - blocked guard + non-pure-text → loop_to_model with blocked message
  - suppression after blocked episode → None, resets state
  - parallel write_todos detection → inject error messages (no decision change)
  - validation error (bad payload) → inject error
  - finalization guard triggers on pure-text with unfinished todos → loop_to_model
  - finalization guard escalates to blocked after 3 retries → loop_to_model + blocked state
  - stale reminder injected at threshold
  - stale reminder NOT injected below threshold
  - stale counter resets on write_todos call
  - finalization_correction flag lifecycle
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from cubepi.agent.types import AfterToolCallContext, AgentContext, AgentToolResult
from cubepi.middleware.todo import (
    STALE_REMINDER_THRESHOLD,
    Todo,
    TodoListMiddleware,
    WriteTodosInput,
    _last_assistant_message,
    _make_write_todos_tool,
    _non_todo_tool_calls,
    _pure_text_assistant_response,
    _submitted_write_todos_calls,
    _todo_validation_errors_local,
)
from cubepi.providers.base import (
    AssistantMessage,
    TextContent,
    ToolCall,
    UserMessage,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_extra(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


def _make_middleware(extra: dict[str, Any] | None = None) -> TodoListMiddleware:
    if extra is None:
        extra = {}
    return TodoListMiddleware(extra_ref=lambda: extra)


def _make_assistant_msg(
    text: str = "",
    tool_calls: list[ToolCall] | None = None,
    stop_reason: str = "end_turn",
) -> AssistantMessage:
    content: list[Any] = []
    if text:
        content.append(TextContent(text=text))
    if tool_calls:
        content.extend(tool_calls)
    return AssistantMessage(content=content, stop_reason=stop_reason)


def _make_tool_call(
    name: str = "write_todos", tool_id: str = "tc-1", args: dict | None = None
) -> ToolCall:
    return ToolCall(id=tool_id, name=name, arguments=args or {})


def _make_agent_context(
    messages: list[Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> AgentContext:
    return AgentContext(
        system_prompt="base",
        messages=messages or [],
        extra=extra or {},
    )


def _make_todo(content: str = "Do something", status: str = "in_progress") -> Todo:
    return Todo(content=content, status=status)


def _make_after_ctx(
    tool_name: str = "write_todos",
    result: AgentToolResult | None = None,
    is_error: bool = False,
    extra: dict[str, Any] | None = None,
) -> AfterToolCallContext:
    if result is None:
        result = AgentToolResult(content=[])
    agent_ctx = _make_agent_context(extra=extra)
    return AfterToolCallContext(
        assistant_message=_make_assistant_msg(),
        tool_call=_make_tool_call(tool_name),
        args=MagicMock(),
        result=result,
        is_error=is_error,
        context=agent_ctx,
    )


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


def test_last_assistant_message_empty() -> None:
    assert _last_assistant_message([]) is None


def test_last_assistant_message_finds_last() -> None:
    msg1 = _make_assistant_msg("first")
    msg2 = _make_assistant_msg("second")
    user = UserMessage(content=[TextContent(text="hi")])
    result = _last_assistant_message([msg1, user, msg2])
    assert result is msg2


def test_submitted_write_todos_calls() -> None:
    tc_write = _make_tool_call("write_todos", "tc-1")
    tc_other = _make_tool_call("bash", "tc-2")
    msg = _make_assistant_msg(tool_calls=[tc_write, tc_other])
    calls = _submitted_write_todos_calls(msg)
    assert len(calls) == 1
    assert calls[0].name == "write_todos"


def test_non_todo_tool_calls() -> None:
    tc_write = _make_tool_call("write_todos", "tc-1")
    tc_bash = _make_tool_call("bash", "tc-2")
    msg = _make_assistant_msg(tool_calls=[tc_write, tc_bash])
    calls = _non_todo_tool_calls(msg)
    assert len(calls) == 1
    assert calls[0].name == "bash"


def test_pure_text_assistant_response_true() -> None:
    msg = _make_assistant_msg("Hello world")
    assert _pure_text_assistant_response(msg) is True


def test_pure_text_assistant_response_false_has_tool() -> None:
    tc = _make_tool_call("bash")
    msg = _make_assistant_msg("Hello", tool_calls=[tc])
    assert _pure_text_assistant_response(msg) is False


def test_pure_text_assistant_response_false_no_text() -> None:
    tc = _make_tool_call("bash")
    msg = _make_assistant_msg(tool_calls=[tc])
    assert _pure_text_assistant_response(msg) is False


# ---------------------------------------------------------------------------
# Tool injection
# ---------------------------------------------------------------------------


def test_tools_contains_write_todos() -> None:
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    assert len(mw.tools) == 1
    assert mw.tools[0].name == "write_todos"


def test_tools_write_todos_description_present() -> None:
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    assert "write_todos" in mw.tools[0].description.lower() or len(mw.tools[0].description) > 50


def test_tools_write_todos_schema() -> None:
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    schema = mw.tools[0].to_definition()
    assert schema.name == "write_todos"
    assert "todos" in schema.parameters.get("properties", {})


# ---------------------------------------------------------------------------
# write_todos tool execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_todos_tool_successful_write() -> None:
    extra: dict[str, Any] = {}
    tool = _make_write_todos_tool(lambda: extra)
    args = WriteTodosInput(todos=[_make_todo("Task 1", "in_progress")])
    result = await tool.execute("tc-1", args)
    assert not result.is_error
    extra_todos = extra.get("todos")
    assert extra_todos is not None
    assert len(extra_todos) == 1
    assert extra_todos[0]["content"] == "Task 1"
    assert extra_todos[0]["status"] == "in_progress"
    # Content should be JSON
    content_text = result.content[0].text  # type: ignore[union-attr]
    payload = json.loads(content_text)
    assert "todos" in payload


@pytest.mark.asyncio
async def test_write_todos_tool_empty_todos_with_unfinished() -> None:
    """Empty list when prior todos have unfinished items → error."""
    prior: list[Todo] = [_make_todo("old task", "in_progress")]
    extra: dict[str, Any] = {"todos": prior}
    tool = _make_write_todos_tool(lambda: extra)
    args = WriteTodosInput(todos=[])
    result = await tool.execute("tc-1", args)
    assert result.is_error is True


@pytest.mark.asyncio
async def test_write_todos_tool_all_completed_adds_reminder() -> None:
    """3+ completed todos add a reminder to the JSON payload."""
    extra: dict[str, Any] = {}
    tool = _make_write_todos_tool(lambda: extra)
    todos = [
        _make_todo("A", "completed"),
        _make_todo("B", "completed"),
        _make_todo("C", "completed"),
    ]
    args = WriteTodosInput(todos=todos)
    result = await tool.execute("tc-1", args)
    assert not result.is_error
    payload = json.loads(result.content[0].text)  # type: ignore[union-attr]
    assert "reminder" in payload


@pytest.mark.asyncio
async def test_write_todos_tool_multiple_in_progress_error() -> None:
    """Multiple in_progress todos → validation error result."""
    extra: dict[str, Any] = {}
    tool = _make_write_todos_tool(lambda: extra)
    args = WriteTodosInput(
        todos=[
            _make_todo("Task 1", "in_progress"),
            _make_todo("Task 2", "in_progress"),
        ]
    )
    result = await tool.execute("tc-1", args)
    assert result.is_error is True


@pytest.mark.asyncio
async def test_write_todos_tool_no_in_progress_with_pending() -> None:
    """Pending todos but no in_progress → validation error."""
    extra: dict[str, Any] = {}
    tool = _make_write_todos_tool(lambda: extra)
    args = WriteTodosInput(
        todos=[
            _make_todo("Task 1", "pending"),
            _make_todo("Task 2", "pending"),
        ]
    )
    result = await tool.execute("tc-1", args)
    assert result.is_error is True


# ---------------------------------------------------------------------------
# transform_system_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_system_prompt_appends() -> None:
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    result = await mw.transform_system_prompt("Base prompt.", ctx=object())
    assert result.startswith("Base prompt.")
    assert "write_todos" in result


@pytest.mark.asyncio
async def test_transform_system_prompt_deterministic() -> None:
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    r1 = await mw.transform_system_prompt("Base.", ctx=object())
    r2 = await mw.transform_system_prompt("Base.", ctx=object())
    assert r1 == r2


# ---------------------------------------------------------------------------
# transform_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_context_no_todos_passthrough() -> None:
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    messages: list[Any] = [UserMessage(content=[TextContent(text="hello")])]
    result = await mw.transform_context(messages, ctx=object())
    assert result == messages


@pytest.mark.asyncio
async def test_transform_context_empty_todos_passthrough() -> None:
    extra: dict[str, Any] = {"todos": []}
    mw = _make_middleware(extra)
    messages: list[Any] = [UserMessage(content=[TextContent(text="hello")])]
    result = await mw.transform_context(messages, ctx=object())
    assert result == messages


@pytest.mark.asyncio
async def test_transform_context_injects_todo_message() -> None:
    todos = [_make_todo("Do task", "in_progress")]
    extra: dict[str, Any] = {"todos": todos}
    mw = _make_middleware(extra)
    messages: list[Any] = []
    result = await mw.transform_context(messages, ctx=object())
    assert len(result) == 1
    injected = result[0]
    assert isinstance(injected, UserMessage)
    assert "[Current todo list]" in injected.content[0].text  # type: ignore[union-attr]
    assert "Do task" in injected.content[0].text  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_transform_context_all_completed_adds_reminder() -> None:
    todos = [
        _make_todo("A", "completed"),
        _make_todo("B", "completed"),
        _make_todo("C", "completed"),
    ]
    extra: dict[str, Any] = {"todos": todos}
    mw = _make_middleware(extra)
    result = await mw.transform_context([], ctx=object())
    text = result[0].content[0].text  # type: ignore[union-attr]
    assert "reminder" in text


@pytest.mark.asyncio
async def test_transform_context_does_not_mutate_original() -> None:
    todos = [_make_todo("X", "in_progress")]
    extra: dict[str, Any] = {"todos": todos}
    mw = _make_middleware(extra)
    orig: list[Any] = [UserMessage(content=[TextContent(text="hi")])]
    result = await mw.transform_context(orig, ctx=object())
    assert len(result) == 2
    assert len(orig) == 1  # original list unchanged


# ---------------------------------------------------------------------------
# after_tool_call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_tool_call_always_returns_none() -> None:
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    ctx = _make_after_ctx(tool_name="write_todos", extra=extra)
    result = await mw.after_tool_call(ctx)
    assert result is None


@pytest.mark.asyncio
async def test_after_tool_call_ignores_other_tools() -> None:
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    ctx = _make_after_ctx(tool_name="bash", extra=extra)
    result = await mw.after_tool_call(ctx)
    assert result is None


# ---------------------------------------------------------------------------
# after_model_response — clean state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_model_no_todos_no_action() -> None:
    """No todos, model has tool calls → None (natural flow)."""
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    response = _make_assistant_msg(tool_calls=[_make_tool_call("bash")])
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_after_model_todos_all_complete_tool_calls_no_action() -> None:
    """All todos completed + model has tool calls → None."""
    extra: dict[str, Any] = {"todos": [_make_todo("A", "completed")]}
    mw = _make_middleware(extra)
    response = _make_assistant_msg(tool_calls=[_make_tool_call("bash")])
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is None


# ---------------------------------------------------------------------------
# after_model_response — finalization guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalization_guard_triggers_on_pure_text_with_unfinished() -> None:
    """Pure text response with unfinished todos → loop_to_model with correction."""
    todos = [_make_todo("Task 1", "in_progress")]
    extra: dict[str, Any] = {"todos": todos}
    mw = _make_middleware(extra)
    response = _make_assistant_msg("Done! I finished everything.")
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is not None
    assert result.decision == "loop_to_model"
    assert len(result.inject_messages) > 0
    assert extra.get("todo_finalization_correction") is True


@pytest.mark.asyncio
async def test_finalization_guard_no_trigger_with_tool_calls() -> None:
    """Unfinished todos but model has tool calls → no guard."""
    todos = [_make_todo("Task 1", "in_progress")]
    extra: dict[str, Any] = {"todos": todos}
    mw = _make_middleware(extra)
    response = _make_assistant_msg(tool_calls=[_make_tool_call("bash")])
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_finalization_guard_escalates_after_3_retries() -> None:
    """3 consecutive guard triggers → escalate to blocked state."""
    todos = [_make_todo("Task 1", "in_progress")]
    extra: dict[str, Any] = {
        "todos": todos,
        "todo_guard_retries": {"finalization": 2},  # already 2 retries
    }
    mw = _make_middleware(extra)
    response = _make_assistant_msg("I'm done!")
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is not None
    assert result.decision == "loop_to_model"
    # Should now be in blocked state
    assert extra.get("todo_guard_blocked") is not None
    assert extra["todo_guard_blocked"]["guard_type"] == "finalization"


@pytest.mark.asyncio
async def test_finalization_guard_retry_count_increments() -> None:
    todos = [_make_todo("Task 1", "in_progress")]
    extra: dict[str, Any] = {"todos": todos}
    mw = _make_middleware(extra)
    response = _make_assistant_msg("I'm done.")
    ctx = _make_agent_context()
    await mw.after_model_response(response, ctx)
    assert extra["todo_guard_retries"].get("finalization", 0) == 1


# ---------------------------------------------------------------------------
# after_model_response — blocked guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_guard_pure_text_stops() -> None:
    """When blocked and model gives pure text → stop decision, clear state."""
    blocked = {"guard_type": "finalization", "message": "Todos still unfinished."}
    extra: dict[str, Any] = {
        "todo_guard_blocked": blocked,
        "todo_guard_retries": {"finalization": 3},
    }
    mw = _make_middleware(extra)
    response = _make_assistant_msg("I cannot continue safely due to todo issues.")
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is not None
    assert result.decision == "stop"
    assert extra.get("todo_guard_blocked") is None
    assert extra.get("todo_guard_suppressed") is True
    assert extra.get("todo_stale_iterations") == 0


@pytest.mark.asyncio
async def test_blocked_guard_non_pure_text_loops() -> None:
    """When blocked and model has tool calls → loop_to_model with blocked message."""
    blocked = {"guard_type": "finalization", "message": "Todos still unfinished."}
    extra: dict[str, Any] = {"todo_guard_blocked": blocked}
    mw = _make_middleware(extra)
    response = _make_assistant_msg(tool_calls=[_make_tool_call("bash")])
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is not None
    assert result.decision == "loop_to_model"
    assert len(result.inject_messages) > 0
    # Message should mention "blocked" or "Todo synchronization"
    inject_text = result.inject_messages[0].content[0].text  # type: ignore[union-attr]
    assert "todo" in inject_text.lower() or "synchronization" in inject_text.lower()


# ---------------------------------------------------------------------------
# after_model_response — suppression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suppression_after_blocked_episode() -> None:
    """After blocked episode resolved, suppression flag allows pass-through."""
    extra: dict[str, Any] = {
        "todo_guard_suppressed": True,
        "todo_guard_retries": {"finalization": 0},
    }
    mw = _make_middleware(extra)
    response = _make_assistant_msg("Continuing now.")
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    # Should reset state and return None (no action)
    assert result is None
    assert extra.get("todo_stale_iterations") == 0


# ---------------------------------------------------------------------------
# after_model_response — parallel write_todos detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_write_todos_detection() -> None:
    """Multiple write_todos calls in one response → inject error messages."""
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    response = _make_assistant_msg(
        tool_calls=[
            _make_tool_call("write_todos", "tc-1"),
            _make_tool_call("write_todos", "tc-2"),
        ]
    )
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is not None
    assert len(result.inject_messages) == 2
    for msg in result.inject_messages:
        assert isinstance(msg, UserMessage)
        assert "parallel" in msg.content[0].text.lower()  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# after_model_response — validation errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_error_multiple_in_progress() -> None:
    """write_todos with 2 in_progress todos → inject error message."""
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    bad_todos = [
        {"content": "A", "status": "in_progress"},
        {"content": "B", "status": "in_progress"},
    ]
    response = _make_assistant_msg(
        tool_calls=[_make_tool_call("write_todos", "tc-1", {"todos": bad_todos})]
    )
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is not None
    assert len(result.inject_messages) > 0
    inject_text = result.inject_messages[0].content[0].text  # type: ignore[union-attr]
    assert "in_progress" in inject_text.lower() or "error" in inject_text.lower()


@pytest.mark.asyncio
async def test_validation_error_empty_content() -> None:
    """write_todos with empty content string → inject error."""
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    bad_todos = [{"content": "  ", "status": "in_progress"}]
    response = _make_assistant_msg(
        tool_calls=[_make_tool_call("write_todos", "tc-1", {"todos": bad_todos})]
    )
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is not None
    assert len(result.inject_messages) > 0


@pytest.mark.asyncio
async def test_validation_error_bad_status() -> None:
    """write_todos with invalid status → inject error."""
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)
    bad_todos = [{"content": "Task", "status": "done"}]  # "done" is not valid
    response = _make_assistant_msg(
        tool_calls=[_make_tool_call("write_todos", "tc-1", {"todos": bad_todos})]
    )
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is not None
    assert len(result.inject_messages) > 0


# ---------------------------------------------------------------------------
# after_model_response — stale reminder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_reminder_triggered_at_threshold() -> None:
    """Stale reminder injected when stale_iterations reaches threshold."""
    todos = [_make_todo("Task 1", "in_progress")]
    extra: dict[str, Any] = {
        "todos": todos,
        "todo_stale_iterations": STALE_REMINDER_THRESHOLD - 1,  # one below threshold
    }
    mw = _make_middleware(extra)
    # A tool call that is NOT write_todos → increments stale counter
    response = _make_assistant_msg(tool_calls=[_make_tool_call("bash", "tc-1")])
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is not None
    assert len(result.inject_messages) > 0
    inject_text = result.inject_messages[0].content[0].text  # type: ignore[union-attr]
    assert "todo" in inject_text.lower()
    assert extra["todo_stale_iterations"] == STALE_REMINDER_THRESHOLD


@pytest.mark.asyncio
async def test_stale_reminder_not_triggered_below_threshold() -> None:
    """No reminder below threshold."""
    todos = [_make_todo("Task 1", "in_progress")]
    extra: dict[str, Any] = {
        "todos": todos,
        "todo_stale_iterations": 2,
    }
    mw = _make_middleware(extra)
    response = _make_assistant_msg(tool_calls=[_make_tool_call("bash", "tc-1")])
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is None
    assert extra["todo_stale_iterations"] == 3


@pytest.mark.asyncio
async def test_stale_counter_resets_on_write_todos() -> None:
    """Stale counter reset to 0 when write_todos is called."""
    todos = [_make_todo("Task 1", "in_progress")]
    extra: dict[str, Any] = {
        "todos": todos,
        "todo_stale_iterations": 10,
    }
    mw = _make_middleware(extra)
    # write_todos call (valid payload)
    good_todos = [{"content": "Task 1", "status": "completed"}]
    response = _make_assistant_msg(
        tool_calls=[_make_tool_call("write_todos", "tc-1", {"todos": good_todos})]
    )
    ctx = _make_agent_context()
    await mw.after_model_response(response, ctx)
    assert extra["todo_stale_iterations"] == 0


@pytest.mark.asyncio
async def test_stale_counter_resets_on_pure_text() -> None:
    """Stale counter reset to 0 on non-tool turn (and finalization guard does not fire
    when no todos)."""
    extra: dict[str, Any] = {
        "todos": [],  # no unfinished todos
        "todo_stale_iterations": 8,
    }
    mw = _make_middleware(extra)
    response = _make_assistant_msg("Pure text turn with no tool calls.")
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    # No finalization guard (no unfinished todos); stale counter should reset
    assert result is None
    assert extra["todo_stale_iterations"] == 0


# ---------------------------------------------------------------------------
# after_model_response — finalization_correction lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalization_correction_clears_when_write_todos_and_finished() -> None:
    """After finalization correction guard fires, write_todos with all completed
    todos clears the finalization_correction flag."""
    todos_completed = [_make_todo("Task 1", "completed")]
    extra: dict[str, Any] = {
        "todos": todos_completed,
        "todo_finalization_correction": True,
    }
    mw = _make_middleware(extra)
    good_todos = [{"content": "Task 1", "status": "completed"}]
    response = _make_assistant_msg(
        tool_calls=[_make_tool_call("write_todos", "tc-1", {"todos": good_todos})]
    )
    ctx = _make_agent_context()
    await mw.after_model_response(response, ctx)
    assert extra.get("todo_finalization_correction") is None


@pytest.mark.asyncio
async def test_finalization_correction_stays_when_still_unfinished() -> None:
    """finalization_correction stays alive while todos remain unfinished."""
    todos_unfinished = [_make_todo("Task 1", "in_progress")]
    extra: dict[str, Any] = {
        "todos": todos_unfinished,
        "todo_finalization_correction": True,
    }
    mw = _make_middleware(extra)
    # write_todos call but todos are still unfinished
    new_todos = [{"content": "Task 1", "status": "in_progress"}]
    response = _make_assistant_msg(
        tool_calls=[_make_tool_call("write_todos", "tc-1", {"todos": new_todos})]
    )
    ctx = _make_agent_context()
    await mw.after_model_response(response, ctx)
    # The flag should still be True (todos still unfinished and write_todos was called)
    # Wait — the logic says: if finalization_correction and (not unfinished OR not has_write_todos)
    # → clear it. In this case: unfinished=True AND has_write_todos=True → condition is False
    # → flag stays.  But actually the code checks: if not unfinished or not has_write_todos → clear
    # Since unfinished=True and has_write_todos=True: both conditions false → flag stays.
    # But wait: extra["todos"] is updated by the tool execute(), not here. So at this point
    # in after_model_response, extra["todos"] is still the old value (in_progress).
    # The validation would fire since there's 1 write_todos call — but the args are valid.
    # Actually the validation checks write_todos args, not what's in extra.
    # The _todo_validation_errors_local sees 1 write_todos call with valid payload → no error.
    # Then: unfinished = _unfinished_todos(extra.get("todos")) = [in_progress item] → True
    # has_write_todos = True
    # The finalization_correction block: if not unfinished(True) or not has_write_todos(True)
    # → neither is False → don't clear. But hmm... the intent is: clear when write_todos called.
    # Let me check the original: if not unfinished or not has_write_todos: clear
    # with unfinished=True and has_write_todos=True → False → DO NOT clear. OK so flag stays.
    assert extra.get("todo_finalization_correction") is True


# ---------------------------------------------------------------------------
# _todo_validation_errors_local (internal helper)
# ---------------------------------------------------------------------------


def test_todo_validation_errors_no_calls_returns_empty() -> None:
    msg = _make_assistant_msg("text only")
    errors = _todo_validation_errors_local(msg, None)
    assert errors == []


def test_todo_validation_errors_valid_payload_returns_empty() -> None:
    good_todos = [{"content": "Task 1", "status": "in_progress"}]
    tc = _make_tool_call("write_todos", "tc-1", {"todos": good_todos})
    msg = _make_assistant_msg(tool_calls=[tc])
    errors = _todo_validation_errors_local(msg, None)
    assert errors == []


def test_todo_validation_errors_multiple_calls_returns_empty() -> None:
    """Multiple write_todos calls are handled by parallel-check, not validation."""
    tc1 = _make_tool_call("write_todos", "tc-1", {"todos": []})
    tc2 = _make_tool_call("write_todos", "tc-2", {"todos": []})
    msg = _make_assistant_msg(tool_calls=[tc1, tc2])
    errors = _todo_validation_errors_local(msg, None)
    assert errors == []  # parallel check is upstream


def test_todo_validation_errors_bad_status() -> None:
    bad_todos = [{"content": "Task", "status": "unknown_status"}]
    tc = _make_tool_call("write_todos", "tc-1", {"todos": bad_todos})
    msg = _make_assistant_msg(tool_calls=[tc])
    errors = _todo_validation_errors_local(msg, None)
    assert len(errors) == 1
    assert "tool_call_id" in errors[0]
    assert "error" in errors[0]


# ---------------------------------------------------------------------------
# Full round-trip: write_todos writes extra, guard reads extra
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_trip_write_then_guard() -> None:
    """Write todos via tool execute, then trigger finalization guard."""
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)

    # Step 1: execute the write_todos tool
    tool = mw.tools[0]
    args = WriteTodosInput(todos=[_make_todo("Important task", "in_progress")])
    await tool.execute("tc-1", args)
    assert extra.get("todos") is not None
    assert extra["todos"][0]["content"] == "Important task"

    # Step 2: pure-text response → should trigger finalization guard
    response = _make_assistant_msg("I've finished everything!")
    ctx = _make_agent_context()
    result = await mw.after_model_response(response, ctx)
    assert result is not None
    assert result.decision == "loop_to_model"
