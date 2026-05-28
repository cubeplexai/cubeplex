"""Tests for command-rule enforcement in the execute middleware.

Covers the v1 behavior:
- ``deny`` matches abort BEFORE the sandbox runs the command and surface as
  is_error=True so cubepi treats the call as a tool failure.
- ``allow`` (or no rule) passes through to ``sandbox.execute``.
- ``confirm`` degrades to deny in v1 because cubepi has no elicit/approve
  channel yet — message is distinct so admins can tell the rule fired.
- Shell chaining cannot smuggle a denied sub-command past an allow.
"""

from __future__ import annotations

import pytest

from cubebox.middleware.sandbox import _make_execute_tool


class _FakeResult:
    def __init__(self, output: str, exit_code: int = 0) -> None:
        self.output = output
        self.exit_code = exit_code


class _FakeSandbox:
    workdir = "/workspace"

    def __init__(self) -> None:
        self.ran: list[str] = []

    async def execute(self, command: str) -> _FakeResult:
        self.ran.append(command)
        return _FakeResult("ok")


@pytest.mark.asyncio
async def test_deny_blocks_and_never_runs() -> None:
    sb = _FakeSandbox()
    tool = _make_execute_tool(sb, command_rules=[{"action": "deny", "pattern": "rm *"}])
    result = await tool.execute("c1", tool.parameters(command="rm -rf /workspace"))
    text = result.content[0].text
    assert "blocked by org policy" in text
    assert result.is_error is True  # surfaces as a tool error, not a success
    assert sb.ran == []  # nothing executed


@pytest.mark.asyncio
async def test_allow_runs() -> None:
    sb = _FakeSandbox()
    tool = _make_execute_tool(sb, command_rules=[{"action": "deny", "pattern": "rm *"}])
    await tool.execute("c1", tool.parameters(command="ls -la"))
    assert sb.ran == ["ls -la"]


@pytest.mark.asyncio
async def test_confirm_degrades_to_deny_in_v1() -> None:
    sb = _FakeSandbox()
    tool = _make_execute_tool(sb, command_rules=[{"action": "confirm", "pattern": "git push *"}])
    result = await tool.execute("c1", tool.parameters(command="git push origin main"))
    text = result.content[0].text
    assert "requires confirmation" in text
    assert result.is_error is True
    assert sb.ran == []


@pytest.mark.asyncio
async def test_chained_denied_subcommand_blocks_whole_call() -> None:
    sb = _FakeSandbox()
    tool = _make_execute_tool(sb, command_rules=[{"action": "deny", "pattern": "rm *"}])
    result = await tool.execute("c1", tool.parameters(command="ls && rm -rf /"))
    assert "blocked by org policy" in result.content[0].text
    assert sb.ran == []
