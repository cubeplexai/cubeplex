import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from cubebox.middleware.todo import (
    STALE_REMINDER_INTERVAL,
    STALE_REMINDER_THRESHOLD,
    TodoListMiddleware,
    _write_todos,
)


def test_todo_middleware_registers_write_todos_tool():
    mw = TodoListMiddleware()
    tool_names = [tool.name for tool in mw.tools]
    assert tool_names == ["write_todos"]


def test_todo_middleware_prompt_reflects_single_in_progress_invariant():
    mw = TodoListMiddleware()

    assert (
        "Unless all tasks are completed, only one task should be in_progress" in mw.tool_description
    )
    assert "Unless all tasks are completed, only one task should be in_progress" in mw.system_prompt
    assert "first task (or tasks)" not in mw.tool_description


def test_write_todos_returns_json_tool_message():
    runtime = SimpleNamespace(tool_call_id="tc-1")
    todos = [
        {"content": "Inspect payload shape", "status": "completed"},
        {"content": "Patch todo middleware", "status": "in_progress"},
    ]

    command = _write_todos(runtime, todos)

    assert command.update is not None
    assert command.update["todos"] == todos
    messages = command.update["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], ToolMessage)
    assert messages[0].tool_call_id == "tc-1"
    assert json.loads(messages[0].content) == {"todos": todos}


def test_write_todos_adds_closeout_reminder_for_completed_three_item_list():
    runtime = SimpleNamespace(tool_call_id="tc-1")
    todos = [
        {"content": "Inspect payload shape", "status": "completed"},
        {"content": "Patch todo middleware", "status": "completed"},
        {"content": "Verify closeout reminder", "status": "completed"},
    ]

    command = _write_todos(runtime, todos)

    payload = json.loads(command.update["messages"][0].content)
    assert payload["todos"] == todos
    assert set(payload) == {"todos", "reminder"}
    assert isinstance(payload["reminder"], str)
    assert payload["reminder"].strip()


def test_write_todos_skips_closeout_reminder_for_short_completed_list():
    runtime = SimpleNamespace(tool_call_id="tc-1")
    todos = [
        {"content": "Inspect payload shape", "status": "completed"},
        {"content": "Patch todo middleware", "status": "completed"},
    ]

    command = _write_todos(runtime, todos)

    payload = json.loads(command.update["messages"][0].content)
    assert payload == {"todos": todos}


def test_write_todos_skips_closeout_reminder_for_unfinished_list():
    runtime = SimpleNamespace(tool_call_id="tc-1")
    todos = [
        {"content": "Inspect payload shape", "status": "completed"},
        {"content": "Patch todo middleware", "status": "in_progress"},
        {"content": "Verify closeout reminder", "status": "pending"},
    ]

    command = _write_todos(runtime, todos)

    payload = json.loads(command.update["messages"][0].content)
    assert payload == {"todos": todos}


def test_todo_middleware_rejects_parallel_write_todos_calls():
    mw = TodoListMiddleware()
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {"todos": [{"content": "One", "status": "pending"}]},
                        "type": "tool_call",
                    },
                    {
                        "id": "tc-2",
                        "name": "write_todos",
                        "args": {"todos": [{"content": "Two", "status": "pending"}]},
                        "type": "tool_call",
                    },
                ],
            )
        ]
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    messages = result["messages"]
    assert len(messages) == 2
    assert all(isinstance(msg, ToolMessage) for msg in messages)
    assert all(msg.status == "error" for msg in messages)
    assert all(
        "Error: The `write_todos` tool should never be called multiple times in parallel."
        in msg.content
        for msg in messages
    )


@pytest.mark.parametrize(
    ("todos", "expected_error"),
    [
        (
            [{"content": "", "status": "pending"}],
            "Error: Todo content cannot be empty.",
        ),
        (
            [
                {"content": "First", "status": "pending"},
                {"content": "Second", "status": "pending"},
            ],
            "Error: Unless all tasks are completed, exactly one todo must be in_progress.",
        ),
        (
            [
                {"content": "First", "status": "in_progress"},
                {"content": "Second", "status": "in_progress"},
            ],
            "Error: Unless all tasks are completed, exactly one todo must be in_progress.",
        ),
    ],
)
def test_todo_middleware_rejects_invalid_write_todos_payloads(todos, expected_error):
    mw = TodoListMiddleware()
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {"todos": todos},
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    messages = result["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], ToolMessage)
    assert messages[0].tool_call_id == "tc-1"
    assert messages[0].status == "error"
    assert expected_error in messages[0].content


@pytest.mark.parametrize(
    "tool_args",
    [
        {},
        {"todos": None},
        {"todos": "not-a-list"},
        {"todos": ["not-a-dict"]},
    ],
)
def test_todo_middleware_rejects_malformed_write_todos_args(tool_args):
    mw = TodoListMiddleware()
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": tool_args,
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert "jump_to" not in result
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], ToolMessage)
    assert result["messages"][0].tool_call_id == "tc-1"
    assert result["messages"][0].status == "error"
    assert "invalid `write_todos` payload" in result["messages"][0].content


def test_todo_middleware_all_completed_list_is_valid():
    mw = TodoListMiddleware()
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {"content": "First", "status": "completed"},
                                {"content": "Second", "status": "completed"},
                            ]
                        },
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result == {"todo_guard_retries": {}, "todo_stale_iterations": 0}


def test_todo_middleware_allows_empty_write_todos_after_completed_prior_state():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "completed"}],
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {"todos": []},
                        "type": "tool_call",
                    }
                ],
            )
        ],
    }

    assert mw.after_model(state, runtime=SimpleNamespace()) == {
        "todo_guard_retries": {},
        "todo_stale_iterations": 0,
    }


def test_todo_middleware_accepts_single_in_progress_in_unfinished_list():
    mw = TodoListMiddleware()
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {"content": "First", "status": "in_progress"},
                                {"content": "Second", "status": "pending"},
                            ]
                        },
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result == {"todo_guard_retries": {}, "todo_stale_iterations": 0}


def test_todo_middleware_accepts_completed_in_progress_pending_transition():
    mw = TodoListMiddleware()
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {"content": "First", "status": "completed"},
                                {"content": "Second", "status": "in_progress"},
                                {"content": "Third", "status": "pending"},
                            ]
                        },
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result == {"todo_guard_retries": {}, "todo_stale_iterations": 0}


def test_todo_middleware_rejects_empty_write_todos_when_prior_state_is_unfinished():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {"todos": []},
                        "type": "tool_call",
                    }
                ],
            )
        ],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert "jump_to" not in result
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], ToolMessage)
    assert result["messages"][0].tool_call_id == "tc-1"
    assert result["messages"][0].status == "error"
    assert "empty list" in result["messages"][0].content
    assert (
        "unfinished todos" in result["messages"][0].content
        or "active items" in result["messages"][0].content
    )


def test_todo_middleware_validates_write_todos_before_stale_guard():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "execute",
                        "args": {"command": "pytest tests/unit/test_middleware_todo.py -v"},
                        "type": "tool_call",
                    },
                    {
                        "id": "tc-2",
                        "name": "write_todos",
                        "args": {"todos": [{"content": "", "status": "pending"}]},
                        "type": "tool_call",
                    },
                ],
            )
        ],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert "jump_to" not in result
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], ToolMessage)
    assert result["messages"][0].tool_call_id == "tc-2"
    assert result["messages"][0].status == "error"
    assert "Todo content cannot be empty" in result["messages"][0].content


def test_todo_middleware_rejects_parallel_write_todos_before_stale_guard():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "execute",
                        "args": {"command": "pytest tests/unit/test_middleware_todo.py -v"},
                        "type": "tool_call",
                    },
                    {
                        "id": "tc-2",
                        "name": "write_todos",
                        "args": {"todos": [{"content": "One", "status": "pending"}]},
                        "type": "tool_call",
                    },
                    {
                        "id": "tc-3",
                        "name": "write_todos",
                        "args": {"todos": [{"content": "Two", "status": "pending"}]},
                        "type": "tool_call",
                    },
                ],
            )
        ],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert "jump_to" not in result
    messages = result["messages"]
    assert len(messages) == 2
    assert all(isinstance(msg, ToolMessage) for msg in messages)
    assert all(msg.status == "error" for msg in messages)
    assert all(
        "Error: The `write_todos` tool should never be called multiple times in parallel."
        in msg.content
        for msg in messages
    )


def _tool_only_ai_message() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": "tc-1",
                "name": "execute",
                "args": {"command": "pytest -v"},
                "type": "tool_call",
            }
        ],
    )


def test_todo_middleware_increments_stale_counter_without_reminder_below_threshold():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "messages": [_tool_only_ai_message()],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert "jump_to" not in result
    assert "messages" not in result
    assert result["todo_stale_iterations"] == 1


def test_todo_middleware_emits_stale_reminder_at_threshold():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "todo_stale_iterations": STALE_REMINDER_THRESHOLD - 1,
        "messages": [_tool_only_ai_message()],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert "jump_to" not in result  # soft reminder, not a hard block
    assert result["todo_stale_iterations"] == STALE_REMINDER_THRESHOLD
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], SystemMessage)
    assert "todo list has not been updated" in result["messages"][0].content


def test_todo_middleware_skips_stale_reminder_between_intervals():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "todo_stale_iterations": STALE_REMINDER_THRESHOLD,
        "messages": [_tool_only_ai_message()],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert "messages" not in result
    assert result["todo_stale_iterations"] == STALE_REMINDER_THRESHOLD + 1


def test_todo_middleware_emits_stale_reminder_again_at_next_interval():
    mw = TodoListMiddleware()
    next_reminder = STALE_REMINDER_THRESHOLD + STALE_REMINDER_INTERVAL
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "todo_stale_iterations": next_reminder - 1,
        "messages": [_tool_only_ai_message()],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert result["todo_stale_iterations"] == next_reminder
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], SystemMessage)


def test_todo_middleware_resets_stale_counter_on_write_todos():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "todo_stale_iterations": 4,
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {"content": "Patch middleware", "status": "completed"},
                                {"content": "Review", "status": "in_progress"},
                            ]
                        },
                        "type": "tool_call",
                    }
                ],
            )
        ],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert result["todo_stale_iterations"] == 0


def test_todo_middleware_does_not_emit_stale_reminder_without_prior_todos():
    mw = TodoListMiddleware()
    state = {
        "messages": [_tool_only_ai_message()],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result == {"todo_guard_retries": {}}


def test_todo_middleware_blocks_pure_text_finalization_with_unfinished_todos():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "messages": [AIMessage(content="Implemented the change and everything is done.")],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], SystemMessage)
    assert result["jump_to"] == "model"
    assert result["todo_finalization_correction"] is True
    assert "unfinished items" in result["messages"][0].content
    assert "Error" not in result["messages"][0].content


def test_todo_middleware_blocks_list_block_finalization_with_unfinished_todos():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "messages": [
            AIMessage(
                content=[{"type": "text", "text": "Implemented the change and everything is done."}]
            )
        ],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], SystemMessage)
    assert result["jump_to"] == "model"
    assert "unfinished items" in result["messages"][0].content


def test_todo_middleware_does_not_block_finalization_when_all_todos_are_completed():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "completed"}],
        "messages": [AIMessage(content="Implemented the change and everything is done.")],
    }

    assert mw.after_model(state, runtime=SimpleNamespace()) == {
        "todo_guard_retries": {},
        "todo_stale_iterations": 0,
    }


def test_todo_middleware_skips_stale_guard_when_write_todos_occurs_in_same_iteration():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "execute",
                        "args": {"command": "pytest tests/unit/test_middleware_todo.py -v"},
                        "type": "tool_call",
                    },
                    {
                        "id": "tc-2",
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {"content": "Patch middleware", "status": "completed"},
                                {"content": "Review results", "status": "in_progress"},
                            ]
                        },
                        "type": "tool_call",
                    },
                ],
            )
        ],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result == {"todo_guard_retries": {}, "todo_stale_iterations": 0}


def test_todo_middleware_escalates_after_repeated_finalization_failures():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "todo_guard_retries": {"finalization": 2},
        "messages": [AIMessage(content="Implemented the change and everything is done.")],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], SystemMessage)
    assert result["jump_to"] == "model"
    assert result["todo_guard_blocked"]["guard_type"] == "finalization"
    assert "unfinished items" in result["todo_guard_blocked"]["message"]
    assert result["todo_guard_retries"] == {"finalization": 3}


def test_todo_middleware_allows_one_blocked_pure_text_explanation_to_end():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "todo_guard_retries": {"finalization": 3},
        "todo_guard_blocked": {
            "guard_type": "finalization",
            "message": (
                "Error: cannot finalize response while todo list still contains "
                "unfinished items. Update the list first."
            ),
        },
        "messages": [
            AIMessage(
                content=(
                    "I couldn't continue safely because the todo list fell out of sync "
                    "with the work that had already happened."
                )
            )
        ],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result == {
        "jump_to": "end",
        "todo_guard_blocked": None,
        "todo_guard_retries": {},
        "todo_guard_suppressed": True,
        "todo_stale_iterations": 0,
        "todo_finalization_correction": None,
    }


def test_todo_middleware_preserves_terminal_blocked_todos_for_next_turn():
    mw = TodoListMiddleware()
    blocked_exit = mw.after_model(
        {
            "todos": [{"content": "Patch middleware", "status": "in_progress"}],
            "todo_guard_retries": {"finalization": 3},
            "todo_guard_blocked": {
                "guard_type": "finalization",
                "message": (
                    "Error: cannot finalize response while todo list still contains "
                    "unfinished items. Update the list first."
                ),
            },
            "messages": [
                AIMessage(
                    content=(
                        "I couldn't continue safely because the todo list fell out of sync "
                        "with the work that had already happened."
                    )
                )
            ],
        },
        runtime=SimpleNamespace(),
    )

    assert blocked_exit is not None

    next_turn_state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "todo_guard_suppressed": blocked_exit["todo_guard_suppressed"],
        "messages": [AIMessage(content="New unrelated request handled.")],
    }

    assert mw.after_model(next_turn_state, runtime=SimpleNamespace()) == {
        "todo_guard_retries": {},
        "todo_guard_suppressed": True,
        "todo_stale_iterations": 0,
        "todo_finalization_correction": None,
    }


def test_todo_middleware_clears_suppression_after_new_write_todos_submission():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "todo_guard_suppressed": True,
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {"content": "Patch middleware", "status": "completed"},
                                {"content": "Review results", "status": "completed"},
                            ]
                        },
                        "type": "tool_call",
                    }
                ],
            )
        ],
    }

    assert mw.after_model(state, runtime=SimpleNamespace()) == {
        "todo_guard_retries": {},
        "todo_guard_suppressed": None,
        "todo_stale_iterations": 0,
    }


def test_todo_middleware_keeps_blocked_mode_when_model_tries_tools_after_escalation():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "todo_guard_retries": {"finalization": 3},
        "todo_guard_blocked": {
            "guard_type": "finalization",
            "message": (
                "Error: cannot finalize response while todo list still contains "
                "unfinished items. Update the list first."
            ),
        },
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "execute",
                        "args": {"command": "pytest tests/unit/test_middleware_todo.py -v"},
                        "type": "tool_call",
                    }
                ],
            )
        ],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert result["jump_to"] == "model"
    assert result["todo_guard_blocked"] == state["todo_guard_blocked"]
    assert result["todo_guard_retries"] == state["todo_guard_retries"]
    assert result["todo_finalization_correction"] is None
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], SystemMessage)
    assert "Do not call any tools" in result["messages"][0].content
    assert "plain-text explanation" in result["messages"][0].content


def test_todo_middleware_allows_brief_closing_after_finalization_correction():
    """After the finalization guard fires and the model correctly updates todos,
    the model's brief closing response passes through normally (no jump to END).
    The guard message instructed the model to keep it short."""
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "completed"}],
        "todo_finalization_correction": True,
        "messages": [AIMessage(content="All tasks completed.")],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert "jump_to" not in result
    assert result["todo_finalization_correction"] is None


def test_todo_middleware_keeps_correction_flag_while_write_todos_is_in_flight():
    """While the model is actively executing the correction (calling write_todos),
    the flag should survive so it's available after the tool executes."""
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "todo_finalization_correction": True,
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {
                            "todos": [{"content": "Patch middleware", "status": "completed"}]
                        },
                        "type": "tool_call",
                    }
                ],
            )
        ],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert "todo_finalization_correction" not in result  # flag stays in state unchanged


def test_todo_middleware_clears_correction_flag_when_model_ignores_instruction():
    """If the model doesn't call write_todos after a finalization correction
    and todos are still unfinished, the flag is cleared and the finalization
    guard fires again."""
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "todo_finalization_correction": True,
        "messages": [AIMessage(content="I decided not to update the todos.")],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    # Flag was cleared, then finalization guard fires again (sets it back)
    assert result is not None
    assert result["jump_to"] == "model"
    assert result["todo_finalization_correction"] is True


@pytest.mark.asyncio
async def test_todo_middleware_aafter_model_uses_shared_impl(monkeypatch):
    mw = TodoListMiddleware()
    calls = []

    def fake_after_model_impl(state):
        calls.append(state)
        return {"messages": ["sentinel"]}

    monkeypatch.setattr(mw, "_after_model_impl", fake_after_model_impl)

    state = {"messages": []}
    result = await mw.aafter_model(state, runtime=SimpleNamespace())

    assert result == {"messages": ["sentinel"]}
    assert calls == [state]


def test_todo_middleware_after_model_declares_jump_targets():
    assert TodoListMiddleware.after_model.__can_jump_to__ == ["model", "end"]
    assert TodoListMiddleware.aafter_model.__can_jump_to__ == ["model", "end"]
