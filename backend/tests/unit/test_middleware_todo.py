import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from cubebox.middleware.todo import TodoListMiddleware, _write_todos


def test_todo_middleware_registers_write_todos_tool():
    mw = TodoListMiddleware()
    tool_names = [tool.name for tool in mw.tools]
    assert tool_names == ["write_todos"]


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
