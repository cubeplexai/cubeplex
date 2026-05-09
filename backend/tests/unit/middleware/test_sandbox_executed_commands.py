"""executed_commands(ws_id, conv_id) reflects what the execute tool actually ran."""

from __future__ import annotations

from typing import Any

import pytest

from cubebox.middleware.sandbox import (
    _create_execute_tool,
    executed_commands,
    reset_executed_commands,
)


class _StubSandbox:
    """Minimal stand-in for the Sandbox interface."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, command: str) -> Any:
        self.calls.append(command)

        class _R:
            output = "stub output"
            exit_code = 0

        return _R()


@pytest.mark.asyncio
async def test_executed_commands_records_each_call() -> None:
    sandbox = _StubSandbox()
    reset_executed_commands()
    tool = _create_execute_tool(sandbox, workspace_id="ws-1", conversation_id="conv-A")

    await tool.ainvoke({"command": "echo hi"})
    await tool.ainvoke({"command": "ls /tmp"})

    assert executed_commands("ws-1", "conv-A") == ["echo hi", "ls /tmp"]


@pytest.mark.asyncio
async def test_executed_commands_isolated_by_conversation() -> None:
    sandbox = _StubSandbox()
    reset_executed_commands()
    tool_a = _create_execute_tool(sandbox, workspace_id="ws-1", conversation_id="conv-A")
    tool_b = _create_execute_tool(sandbox, workspace_id="ws-1", conversation_id="conv-B")

    await tool_a.ainvoke({"command": "in A"})
    await tool_b.ainvoke({"command": "in B"})

    assert executed_commands("ws-1", "conv-A") == ["in A"]
    assert executed_commands("ws-1", "conv-B") == ["in B"]


@pytest.mark.asyncio
async def test_ring_buffer_caps_at_50_entries() -> None:
    sandbox = _StubSandbox()
    reset_executed_commands()
    tool = _create_execute_tool(sandbox, workspace_id="ws-1", conversation_id="conv-cap")

    for i in range(60):
        await tool.ainvoke({"command": f"cmd-{i}"})

    history = executed_commands("ws-1", "conv-cap")
    assert len(history) == 50
    assert history[0] == "cmd-10"
    assert history[-1] == "cmd-59"


class _BlockingSandbox:
    """Sandbox stand-in that simulates a safety-policy block (non-zero exit)."""

    async def execute(self, command: str) -> Any:
        class _R:
            output = "blocked by policy"
            exit_code = 126

        return _R()


@pytest.mark.asyncio
async def test_blocked_commands_are_not_recorded() -> None:
    """Sandbox-rejected commands (exit_code != 0) must NOT appear in
    executed_commands. Otherwise the adversarial gate test cannot
    distinguish 'agent tried it but sandbox refused' from 'sandbox
    actually ran it'."""
    sandbox = _BlockingSandbox()
    reset_executed_commands()
    tool = _create_execute_tool(sandbox, workspace_id="ws-1", conversation_id="conv-blk")

    await tool.ainvoke({"command": "rm -rf /"})

    assert executed_commands("ws-1", "conv-blk") == []
