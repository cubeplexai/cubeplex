"""Unit tests for SandboxMiddleware (M3.c.1)."""

from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, call

import pytest
from cubeloop.agent.types import AfterToolCallContext, AgentContext, AgentTool, AgentToolResult
from cubeloop.middleware import ToolResultLimitMiddleware
from cubeloop.providers.base import AssistantMessage, TextContent, ToolCall

from cubeplex.middleware._compose import compose_after_tool_call
from cubeplex.middleware.sandbox import (
    EXECUTE_RESULT_SPILL_CHARS,
    SandboxMiddleware,
    _append_sandbox_log,
    _EditFileArgs,
    _EditSpec,
    _ExecuteArgs,
    _FileReadArgs,
    _first_changed_line,
    _make_edit_file_tool,
    _make_execute_tool,
    _make_file_read_tool,
    _make_monitor_tool,
    _make_write_file_tool,
    _MonitorArgs,
    _normalize_for_fuzzy,
    _TaskCommandBinding,
    _WriteFileArgs,
)
from cubeplex.prompts.sandbox import SANDBOX_PROMPT_TEMPLATE

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_TOOL_NAMES = {"execute", "kill_execute", "monitor", "write", "edit", "read"}


def _make_sandbox(workdir: str = "/sandbox/work") -> MagicMock:
    """Return a minimal sandbox mock."""
    sandbox = MagicMock()
    sandbox.workdir = workdir

    async def _acknowledge(handle: Any, cursor: str) -> None:
        handle.log_cursor = cursor

    sandbox.acknowledge_output = AsyncMock(side_effect=_acknowledge)
    return sandbox


def _make_middleware(**kwargs: Any) -> SandboxMiddleware:
    """Build a SandboxMiddleware with minimal mock dependencies."""
    defaults: dict[str, Any] = {
        "sandbox": _make_sandbox(),
        "conversation_id": "conv-test",
        "workspace_id": "ws-test",
    }
    defaults.update(kwargs)
    return SandboxMiddleware(**defaults)


def _text(result: AgentToolResult) -> str:
    """Extract text from the first TextContent in a result."""
    blocks = [c for c in result.content if isinstance(c, TextContent)]
    assert blocks, "Expected at least one TextContent in result"
    return blocks[0].text


# ---------------------------------------------------------------------------
# tools property
# ---------------------------------------------------------------------------


def test_tools_returns_non_empty_list() -> None:
    mw = _make_middleware()
    assert len(mw.tools) > 0


def test_tools_returns_agent_tool_instances() -> None:
    mw = _make_middleware()
    for tool in mw.tools:
        assert isinstance(tool, AgentTool)


def test_tool_names_are_stable() -> None:
    """Sandbox tool names are part of a stable prompt-cache-prefix contract.

    The set of registered tool names contributes to the cache-eligible
    prefix of every model call; changing or reordering these names
    invalidates prompt caches across all existing conversations, so this
    set should not change without an explicit migration plan.
    """
    mw = _make_middleware()
    names = {t.name for t in mw.tools}
    assert names == _EXPECTED_TOOL_NAMES


def test_tools_property_returns_fresh_list_each_time() -> None:
    mw = _make_middleware()
    list1 = mw.tools
    list2 = mw.tools
    assert list1 is not list2
    # Same objects inside
    assert list1[0] is list2[0]


def test_all_tools_have_callable_execute() -> None:
    mw = _make_middleware()
    for tool in mw.tools:
        assert callable(tool.execute), f"execute not callable on tool '{tool.name}'"


def test_all_tools_have_parameter_schemas() -> None:
    mw = _make_middleware()
    for tool in mw.tools:
        schema = tool.parameters.model_json_schema()
        assert "properties" in schema, f"No properties in schema for tool '{tool.name}'"


# ---------------------------------------------------------------------------
# transform_system_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_system_prompt_appends_sandbox_section() -> None:
    mw = _make_middleware(sandbox=_make_sandbox(workdir="/work"))
    result = await mw.transform_system_prompt("You are a helpful assistant.", ctx=object())
    expected_section = SANDBOX_PROMPT_TEMPLATE.format(workdir="/work")
    assert expected_section in result
    assert result.startswith("You are a helpful assistant.")


@pytest.mark.asyncio
async def test_transform_system_prompt_includes_workdir() -> None:
    workdir = "/custom/sandbox/dir"
    mw = _make_middleware(sandbox=_make_sandbox(workdir=workdir))
    result = await mw.transform_system_prompt("base prompt", ctx=object())
    assert workdir in result


@pytest.mark.asyncio
async def test_transform_system_prompt_with_empty_base() -> None:
    """Empty system prompt should not produce a leading double-newline."""
    mw = _make_middleware(sandbox=_make_sandbox(workdir="/work"))
    result = await mw.transform_system_prompt("", ctx=object())
    assert not result.startswith("\n\n")
    assert SANDBOX_PROMPT_TEMPLATE.format(workdir="/work") in result


@pytest.mark.asyncio
async def test_transform_system_prompt_idempotent_same_input() -> None:
    """Same inputs always produce the same output (cache-stable)."""
    mw = _make_middleware(sandbox=_make_sandbox(workdir="/work"))
    base = "You are a helpful assistant."
    result1 = await mw.transform_system_prompt(base, ctx=object())
    result2 = await mw.transform_system_prompt(base, ctx=object())
    assert result1 == result2


@pytest.mark.asyncio
async def test_transform_system_prompt_separator_when_non_empty() -> None:
    mw = _make_middleware(sandbox=_make_sandbox(workdir="/work"))
    result = await mw.transform_system_prompt("existing prompt", ctx=object())
    # Separator between existing prompt and new section
    assert "\n\nexisting prompt" not in result  # existing is first, section appended
    assert "existing prompt\n\n" in result


# ---------------------------------------------------------------------------
# execute tool — delegation and audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_delegates_to_sandbox() -> None:
    sandbox = _make_sandbox()
    exec_result = MagicMock()
    exec_result.output = "hello world"
    exec_result.exit_code = 0
    sandbox.execute = AsyncMock(return_value=exec_result)

    tool = _make_execute_tool(sandbox)
    args = _ExecuteArgs(command="echo hello world", description="Echo a greeting")
    result = await tool.execute("tc-1", args, signal=None, on_update=None)

    sandbox.execute.assert_called_once_with("echo hello world", timeout=3600, on_chunk=ANY)
    assert isinstance(result, AgentToolResult)
    assert "hello world" in _text(result)


@pytest.mark.asyncio
async def test_execute_tool_appends_exit_code_on_failure() -> None:
    sandbox = _make_sandbox()
    exec_result = MagicMock()
    exec_result.output = "command not found"
    exec_result.exit_code = 127
    sandbox.execute = AsyncMock(return_value=exec_result)

    tool = _make_execute_tool(sandbox)
    args = _ExecuteArgs(command="nonexistent", description="Run a missing command")
    result = await tool.execute("tc-2", args)

    text = _text(result)
    assert "command not found" in text
    assert "[exit code: 127]" in text


@pytest.mark.asyncio
async def test_execute_tool_no_exit_code_suffix_on_success() -> None:
    sandbox = _make_sandbox()
    exec_result = MagicMock()
    exec_result.output = "ok"
    exec_result.exit_code = 0
    sandbox.execute = AsyncMock(return_value=exec_result)

    tool = _make_execute_tool(sandbox)
    args = _ExecuteArgs(command="true", description="Succeed immediately")
    result = await tool.execute("tc-3", args)

    assert "[exit code:" not in _text(result)


@pytest.mark.asyncio
async def test_execute_tool_timeout_returns_error_result() -> None:
    """A timed-out command is a tool result the model can retry from, not a run crash."""
    sandbox = _make_sandbox()
    exec_result = MagicMock()
    exec_result.output = "[timeout]"
    exec_result.exit_code = -1
    sandbox.execute = AsyncMock(return_value=exec_result)

    tool = _make_execute_tool(sandbox)
    args = _ExecuteArgs(command="sleep 999", description="Sleep past timeout")
    result = await tool.execute("tc-timeout", args)

    sandbox.execute.assert_called_once_with("sleep 999", timeout=3600, on_chunk=ANY)
    text = _text(result)
    assert text.startswith("[timeout]")
    assert "3600s" in text
    assert result.is_error is True


@pytest.mark.asyncio
async def test_execute_tool_forwards_custom_timeout() -> None:
    sandbox = _make_sandbox()
    exec_result = MagicMock()
    exec_result.output = "ok"
    exec_result.exit_code = 0
    sandbox.execute = AsyncMock(return_value=exec_result)

    tool = _make_execute_tool(sandbox)
    args = _ExecuteArgs(
        command="pip install foo",
        description="Install a package",
        timeout_seconds=300,
    )
    await tool.execute("tc-custom", args)

    sandbox.execute.assert_called_once_with("pip install foo", timeout=300, on_chunk=ANY)


def test_execute_args_requires_description() -> None:
    """description is required so the chat UI can show intent instead of the raw command."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _ExecuteArgs(command="ls")

    args = _ExecuteArgs(command="ls", description="List files")
    assert args.description == "List files"


def test_execute_args_schema_tells_model_description_is_shown_in_chat() -> None:
    schema = _ExecuteArgs.model_json_schema()
    props = schema["properties"]
    assert "description" in props
    assert "description" in schema["required"]
    field_doc = props["description"]["description"].lower()
    assert "chat" in field_doc
    # Property order is what models follow when streaming tool-call JSON.
    # description first means the chat chip can render before a long command.
    assert list(props)[0] == "description"
    assert schema["required"][0] == "description"
    assert "first" in field_doc


def test_execute_args_rejects_timeout_above_max() -> None:
    from pydantic import ValidationError

    from cubeplex.config import MAX_COMMAND_TIMEOUT_SECONDS

    with pytest.raises(ValidationError):
        _ExecuteArgs(
            command="sleep 1",
            description="Sleep briefly",
            timeout_seconds=MAX_COMMAND_TIMEOUT_SECONDS + 1,
        )


def test_execute_args_accepts_timeout_at_max() -> None:
    args = _ExecuteArgs(
        command="pip install foo",
        description="Install a package",
        timeout_seconds=1800,
    )
    assert args.timeout_seconds == 1800


@pytest.mark.asyncio
async def test_execute_tool_maps_timeout_exception_to_result() -> None:
    sandbox = _make_sandbox()
    sandbox.execute = AsyncMock(side_effect=TimeoutError("timed out"))

    tool = _make_execute_tool(sandbox)
    result = await tool.execute(
        "tc-to",
        _ExecuteArgs(command="gh issue list", description="List GitHub issues"),
    )

    text = _text(result)
    assert text.startswith("[timeout]")
    assert "3600s" in text
    assert result.is_error is True


@pytest.mark.asyncio
async def test_execute_tool_streams_on_update_while_running() -> None:
    sandbox = _make_sandbox()

    async def _run(
        command: str,
        *,
        timeout: int | None = None,
        on_chunk: Any = None,
        **kwargs: Any,
    ) -> Any:
        del command, timeout, kwargs
        if on_chunk is not None:
            on_chunk("hello ")
            on_chunk("world")
        result = MagicMock()
        result.output = "hello world"
        result.exit_code = 0
        return result

    sandbox.execute = _run
    updates: list[AgentToolResult] = []
    tool = _make_execute_tool(sandbox)
    result = await tool.execute(
        "tc-stream",
        _ExecuteArgs(command="echo hello world", description="Echo greeting"),
        on_update=updates.append,
    )
    assert updates
    assert all(
        isinstance(u.details, dict) and u.details.get("status") == "running" for u in updates
    )
    assert isinstance(result.details, dict)
    assert result.details.get("status") == "exited"
    assert "hello world" in _text(result)


@pytest.mark.asyncio
async def test_execute_background_rejects_shell_ampersand() -> None:
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    tool = _make_execute_tool(sandbox)
    result = await tool.execute(
        "tc-amp",
        _ExecuteArgs(command="sleep 5 &", description="Bad background", background=True),
    )
    assert result.is_error is True
    assert "background=true" in _text(result)
    sandbox.start.assert_not_called()


@pytest.mark.asyncio
async def test_execute_background_errors_when_driver_cannot() -> None:
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=False)
    tool = _make_execute_tool(sandbox)
    result = await tool.execute(
        "tc-nobg",
        _ExecuteArgs(command="sleep 5", description="Sleep", background=True),
    )
    assert result.is_error is True
    sandbox.start.assert_not_called()


@pytest.mark.asyncio
async def test_execute_background_returns_command_id_before_exit() -> None:
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.workdir = "/workspace"
    from cubeplex.sandbox.base import ProcessHandle

    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    tool = _make_execute_tool(sandbox)
    result = await tool.execute(
        "tc-bg",
        _ExecuteArgs(command="sleep 30", description="Sleep in background", background=True),
    )
    assert result.is_error is not True
    assert isinstance(result.details, dict)
    cid = result.details.get("command_id")
    assert isinstance(cid, str) and cid.startswith("scmd-")
    assert result.details.get("status") == "running"
    sandbox.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_background_hands_off_before_returning_task_identity() -> None:
    from cubeplex.middleware.sandbox import _ReservedCommand
    from cubeplex.sandbox.base import ProcessHandle

    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    live: dict[str, tuple[ProcessHandle, bool]] = {}
    order: list[str] = []

    async def _reserve(**_kwargs: Any) -> _ReservedCommand:
        order.append("reserve")
        return _ReservedCommand(command_id="scmd-durable", task_id="task-durable")

    async def _handoff(command_id: str) -> bool:
        order.append(f"handoff:{command_id}")
        live.pop(command_id)
        return True

    tool = _make_execute_tool(
        sandbox,
        live=live,
        persist_reserve=_reserve,
        persist_running=AsyncMock(),
        handoff=_handoff,
    )

    result = await tool.execute(
        "tc-bg-task",
        _ExecuteArgs(command="sleep 30", description="Durable build", background=True),
    )

    assert order == ["reserve", "handoff:scmd-durable"]
    assert isinstance(result.details, dict)
    assert result.details["status"] == "running"
    assert result.details["task_id"] == "task-durable"
    assert result.details["command_id"] == "scmd-durable"
    assert result.details["notification"] == "once"
    assert result.details["result_pending"] is True
    assert 'wait_for_tasks=["task-durable"]' in _text(result)
    assert "do not start polling commands" in _text(result).lower()
    assert live == {}


@pytest.mark.asyncio
async def test_execute_background_schedules_requested_timeout() -> None:
    from cubeplex.sandbox.base import ProcessHandle

    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    handle = ProcessHandle(command_id="", provider_ref="p1")
    sandbox.start = AsyncMock(return_value=handle)
    deadlines: dict[str, asyncio.Task[None]] = {}
    tool = _make_execute_tool(sandbox, deadline_tasks=deadlines)

    result = await tool.execute(
        "tc-bg-timeout",
        _ExecuteArgs(
            command="sleep 30",
            description="Sleep in background",
            background=True,
            timeout_seconds=7,
        ),
    )

    assert isinstance(result.details, dict)
    command_id = result.details["command_id"]
    assert handle.deadline_at is not None
    assert command_id in deadlines
    assert sandbox.start.await_args.kwargs["timeout"] == 7
    deadline_task = deadlines[command_id]
    deadline_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await deadline_task


@pytest.mark.asyncio
async def test_kill_execute_stops_live_handle() -> None:
    from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot

    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.workdir = "/workspace"
    sandbox.start = AsyncMock(return_value=ProcessHandle("", "p1"))
    sandbox.kill = AsyncMock()
    sandbox.poll = AsyncMock(return_value=ProcessSnapshot(status="killed"))
    live: dict[str, tuple[ProcessHandle, bool]] = {}
    execute = _make_execute_tool(sandbox, live=live)
    started = await execute.execute(
        "tc-k",
        _ExecuteArgs(command="sleep 9", description="Sleep", background=True),
    )
    cid = started.details["command_id"]  # type: ignore[index]
    from cubeplex.middleware.sandbox import _KillExecuteArgs, _make_kill_execute_tool

    killer = _make_kill_execute_tool(sandbox, live)
    killed = await killer.execute("tc-kill", _KillExecuteArgs(command_id=str(cid)))
    sandbox.kill.assert_awaited_once()
    assert "killed" in _text(killed)
    assert cid not in live


@pytest.mark.asyncio
async def test_kill_execute_retains_handle_until_interrupt_is_confirmed() -> None:
    from cubeplex.middleware.sandbox import _KillExecuteArgs, _make_kill_execute_tool
    from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot

    sandbox = _make_sandbox()
    sandbox.kill = AsyncMock()
    sandbox.poll = AsyncMock(return_value=ProcessSnapshot(status="running"))
    handle = ProcessHandle(command_id="scmd-live", provider_ref="p1")
    live = {"scmd-live": (handle, True)}
    persist_killed = AsyncMock()
    killer = _make_kill_execute_tool(
        sandbox,
        live,
        persist_killed=persist_killed,
    )

    result = await killer.execute("tc-kill", _KillExecuteArgs(command_id="scmd-live"))

    assert result.is_error is True
    assert "scmd-live" in live
    persist_killed.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_background_cap_is_atomic() -> None:
    from cubeplex.middleware.sandbox import MAX_LIVE_BACKGROUND_COMMANDS
    from cubeplex.sandbox.base import ProcessHandle

    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.workdir = "/workspace"
    started = 0

    async def _slow_start(command: str, **kwargs: Any) -> ProcessHandle:
        del command, kwargs
        nonlocal started
        await asyncio.sleep(0.02)
        started += 1
        return ProcessHandle("", f"p{started}")

    sandbox.start = _slow_start
    live: dict[str, tuple[Any, bool]] = {}
    lock = asyncio.Lock()
    tool = _make_execute_tool(sandbox, live=live, live_lock=lock)
    results = await asyncio.gather(
        *[
            tool.execute(
                f"tc-cap-{i}",
                _ExecuteArgs(command="sleep 1", description="bg", background=True),
            )
            for i in range(MAX_LIVE_BACKGROUND_COMMANDS + 1)
        ]
    )
    ok = [r for r in results if not r.is_error]
    errors = [r for r in results if r.is_error]
    assert len(ok) == MAX_LIVE_BACKGROUND_COMMANDS
    assert len(errors) == 1
    assert started == MAX_LIVE_BACKGROUND_COMMANDS


@pytest.mark.asyncio
async def test_auto_background_quota_falls_back_to_foreground() -> None:
    from cubeplex.middleware.sandbox import MAX_LIVE_BACKGROUND_COMMANDS
    from cubeplex.sandbox.base import ExecuteResult, ProcessHandle

    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock()
    sandbox.execute = AsyncMock(return_value=ExecuteResult(output="files", exit_code=0))
    live = {
        f"scmd-{index}": (ProcessHandle(f"scmd-{index}", f"p-{index}"), False)
        for index in range(MAX_LIVE_BACKGROUND_COMMANDS)
    }
    tool = _make_execute_tool(sandbox, live=live)

    result = await tool.execute(
        "tc-foreground-at-cap",
        _ExecuteArgs(command="ls", description="List files"),
    )

    assert result.details == {"status": "exited"}
    assert _text(result) == "files"
    sandbox.execute.assert_awaited_once()
    sandbox.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_background_reserve_failure_does_not_start() -> None:
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock()

    async def _boom(**kwargs: Any) -> bool:
        del kwargs
        raise RuntimeError("db down")

    tool = _make_execute_tool(sandbox, persist_reserve=_boom)
    result = await tool.execute(
        "tc-fail",
        _ExecuteArgs(command="sleep 1", description="bg", background=True),
    )
    assert result.is_error is True
    sandbox.start.assert_not_called()


@pytest.mark.asyncio
async def test_kill_execute_keeps_handle_if_kill_fails() -> None:
    from cubeplex.sandbox.base import ProcessHandle

    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.workdir = "/workspace"
    sandbox.start = AsyncMock(return_value=ProcessHandle("", "p1"))
    sandbox.kill = AsyncMock(side_effect=RuntimeError("interrupt failed"))
    live: dict[str, tuple[Any, bool]] = {}
    execute = _make_execute_tool(sandbox, live=live)
    started = await execute.execute(
        "tc-kf",
        _ExecuteArgs(command="sleep 9", description="Sleep", background=True),
    )
    cid = started.details["command_id"]  # type: ignore[index]
    from cubeplex.middleware.sandbox import _KillExecuteArgs, _make_kill_execute_tool

    killer = _make_kill_execute_tool(sandbox, live)
    killed = await killer.execute("tc-kill", _KillExecuteArgs(command_id=str(cid)))
    assert killed.is_error is True
    assert cid in live


@pytest.mark.asyncio
async def test_kill_execute_falls_back_to_conversation_command_index() -> None:
    from cubeplex.middleware.sandbox import _KillExecuteArgs, _make_kill_execute_tool

    sandbox = _make_sandbox()
    seen: list[str] = []

    async def _kill_persisted(command_id: str) -> bool:
        seen.append(command_id)
        return True

    killer = _make_kill_execute_tool(
        sandbox,
        {},
        kill_persisted=_kill_persisted,
    )
    result = await killer.execute(
        "tc-kill-durable",
        _KillExecuteArgs(command_id="scmd-durable"),
    )
    assert result.is_error is not True
    assert seen == ["scmd-durable"]
    assert "stop requested" in _text(result)
    assert result.details == {"status": "stopping", "command_id": "scmd-durable"}


@pytest.mark.asyncio
async def test_mark_running_cas_miss_kills_started_process() -> None:
    from cubeplex.sandbox.base import ProcessHandle

    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.workdir = "/workspace"
    sandbox.kill = AsyncMock()

    async def _start(
        command: str,
        *,
        on_started: Any = None,
        **kwargs: Any,
    ) -> ProcessHandle:
        del command, kwargs
        if on_started is not None:
            maybe = on_started("p-cas")
            if inspect.isawaitable(maybe):
                await maybe
        return ProcessHandle("", "p-cas")

    sandbox.start = _start
    live: dict[str, tuple[Any, bool]] = {}

    async def _reserve(**kwargs: Any) -> bool:
        del kwargs
        return True

    async def _running(command_id: str, ref: str) -> None:
        del command_id, ref
        raise RuntimeError("cas missed")

    async def _killed(command_id: str) -> None:
        del command_id
        return None

    tool = _make_execute_tool(
        sandbox,
        live=live,
        persist_reserve=_reserve,
        persist_running=_running,
        persist_killed=_killed,
    )
    result = await tool.execute(
        "tc-cas",
        _ExecuteArgs(command="sleep 1", description="bg", background=True),
    )
    assert result.is_error is True
    sandbox.kill.assert_awaited_once()
    assert live == {}


@pytest.mark.asyncio
async def test_auto_background_promotes_long_command(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.local import LocalSandbox

    monkeypatch.setattr(sandbox_mod, "AUTO_BACKGROUND_SECONDS", 0.15)
    sandbox = LocalSandbox(workdir=str(tmp_path))
    tool = _make_execute_tool(sandbox)
    result = await tool.execute(
        "tc-auto",
        _ExecuteArgs(command="sleep 2 && echo done", description="Long job"),
    )
    assert result.is_error is not True
    assert isinstance(result.details, dict)
    assert result.details.get("status") == "running"
    cid = result.details.get("command_id")
    assert isinstance(cid, str) and cid.startswith("scmd-")


def test_auto_background_default_wait_is_one_minute() -> None:
    from cubeplex.middleware.sandbox import AUTO_BACKGROUND_SECONDS

    assert AUTO_BACKGROUND_SECONDS == 60


@pytest.mark.asyncio
async def test_auto_background_preserves_deadline_when_notifications_are_disabled(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.base import ProcessHandle
    from cubeplex.sandbox.local import LocalSandbox

    monkeypatch.setattr(sandbox_mod, "AUTO_BACKGROUND_SECONDS", 0.05)
    sandbox = LocalSandbox(workdir=str(tmp_path))
    live: dict[str, tuple[ProcessHandle, bool]] = {}
    deadlines: dict[str, asyncio.Task[None]] = {}
    reservations: list[dict[str, Any]] = []

    async def _reserve(**kwargs: Any) -> bool:
        reservations.append(kwargs)
        return True

    tool = _make_execute_tool(
        sandbox,
        live=live,
        persist_reserve=_reserve,
        deadline_tasks=deadlines,
    )
    result = await tool.execute(
        "tc-auto-server",
        _ExecuteArgs(
            command="sleep 30 && true",
            description="Development server",
            notify_on_complete=False,
        ),
    )

    command_id = result.details["command_id"]  # type: ignore[index]
    assert reservations[0]["notify_on_complete"] is False
    assert reservations[0]["timeout_seconds"] == 3600
    assert reservations[0]["monitor_deadline_at"] is not None
    assert live[str(command_id)][1] is False
    assert live[str(command_id)][0].deadline_at is not None
    await sandbox.kill(live[str(command_id)][0])
    deadline_task = deadlines[str(command_id)]
    deadline_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await deadline_task


@pytest.mark.asyncio
async def test_auto_background_hands_off_instead_of_waiting_at_run_end(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.middleware.sandbox import _ReservedCommand
    from cubeplex.sandbox.base import ProcessHandle
    from cubeplex.sandbox.local import LocalSandbox

    monkeypatch.setattr(sandbox_mod, "AUTO_BACKGROUND_SECONDS", 0.05)
    sandbox = LocalSandbox(workdir=str(tmp_path))
    live: dict[str, tuple[ProcessHandle, bool]] = {}
    handed_off: list[str] = []

    async def _reserve(**_kwargs: Any) -> _ReservedCommand:
        return _ReservedCommand(command_id="scmd-auto", task_id="task-auto")

    async def _handoff(command_id: str) -> bool:
        handed_off.append(command_id)
        live.pop(command_id)
        return True

    tool = _make_execute_tool(
        sandbox,
        live=live,
        persist_reserve=_reserve,
        persist_running=AsyncMock(),
        handoff=_handoff,
    )

    result = await tool.execute(
        "tc-auto-task",
        _ExecuteArgs(command="sleep 30 && true", description="Durable build"),
    )

    assert handed_off == ["scmd-auto"]
    assert result.details["task_id"] == "task-auto"  # type: ignore[index]
    assert 'wait_for_tasks=["task-auto"]' in _text(result)
    assert live == {}


@pytest.mark.asyncio
async def test_explicit_background_has_configured_default_deadline() -> None:
    from cubeplex.sandbox.base import ProcessHandle

    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    sandbox.upload = AsyncMock()
    deadlines: dict[str, asyncio.Task[None]] = {}
    reservations: list[dict[str, Any]] = []

    async def _reserve(**kwargs: Any) -> bool:
        reservations.append(kwargs)
        return True

    tool = _make_execute_tool(
        sandbox,
        persist_reserve=_reserve,
        deadline_tasks=deadlines,
    )
    result = await tool.execute(
        "tc-server",
        _ExecuteArgs(
            command="python -m http.server",
            description="Development server",
            background=True,
            notify_on_complete=False,
        ),
    )

    assert result.is_error is not True
    assert reservations[0]["timeout_seconds"] == 3600
    assert reservations[0]["monitor_deadline_at"] is not None
    assert sandbox.start.await_args.kwargs["timeout"] == 3600
    assert len(deadlines) == 1
    deadline_task = next(iter(deadlines.values()))
    deadline_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await deadline_task


@pytest.mark.asyncio
async def test_deadline_task_persists_exit_observed_before_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot

    async def _immediate_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(sandbox_mod.asyncio, "sleep", _immediate_sleep)
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    sandbox.poll = AsyncMock(return_value=ProcessSnapshot(status="exited", exit_code=7))
    sandbox.kill = AsyncMock()
    persisted = asyncio.Event()
    exits: list[tuple[str, int | None, bool]] = []

    async def _persist_exited(
        command_id: str,
        exit_code: int | None,
        notify: bool,
    ) -> None:
        exits.append((command_id, exit_code, notify))
        persisted.set()

    live: dict[str, tuple[ProcessHandle, bool]] = {}
    tool = _make_execute_tool(
        sandbox,
        live=live,
        persist_exited=_persist_exited,
    )
    result = await tool.execute(
        "tc-deadline-exit",
        _ExecuteArgs(
            command="long command",
            description="Long command",
            background=True,
            timeout_seconds=1,
        ),
    )
    await asyncio.wait_for(persisted.wait(), timeout=1)

    command_id = str(result.details["command_id"])  # type: ignore[index]
    assert exits == [(command_id, 7, True)]
    assert command_id not in live
    sandbox.kill.assert_not_called()


@pytest.mark.asyncio
async def test_auto_background_honors_short_explicit_timeout(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.local import LocalSandbox

    monkeypatch.setattr(sandbox_mod, "AUTO_BACKGROUND_SECONDS", 2)
    sandbox = LocalSandbox(workdir=str(tmp_path))
    tool = _make_execute_tool(sandbox)
    result = await tool.execute(
        "tc-timeout",
        _ExecuteArgs(command="sleep 3", description="Short timeout", timeout_seconds=1),
    )
    assert result.is_error is True
    assert result.details is None
    assert "Command exceeded 1s and was killed" in _text(result)


@pytest.mark.asyncio
async def test_auto_background_preserves_timeout_after_handoff(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.base import ProcessHandle
    from cubeplex.sandbox.local import LocalSandbox

    monkeypatch.setattr(sandbox_mod, "AUTO_BACKGROUND_SECONDS", 0.05)
    sandbox = LocalSandbox(workdir=str(tmp_path))
    middleware = SandboxMiddleware(sandbox=sandbox)
    execute = next(tool for tool in middleware.tools if tool.name == "execute")
    result = await execute.execute(
        "tc-handoff-timeout",
        _ExecuteArgs(command="sleep 30 && true", description="Timed job", timeout_seconds=1),
    )
    assert isinstance(result.details, dict)
    assert result.details["status"] == "running"
    provider_ref = next(iter(sandbox._bg))
    await asyncio.sleep(1.1)

    handle = ProcessHandle(command_id=str(result.details["command_id"]), provider_ref=provider_ref)
    assert (await sandbox.poll(handle)).status == "killed"

    notices = await middleware.on_run_end(AgentContext(system_prompt="", messages=[]))

    assert notices is None


@pytest.mark.asyncio
async def test_auto_background_persists_consumed_log_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.base import ExecuteResult, ProcessHandle, ProcessSnapshot

    monkeypatch.setattr(sandbox_mod, "AUTO_BACKGROUND_SECONDS", 0)
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    sandbox.poll = AsyncMock(
        return_value=ProcessSnapshot(
            status="running",
            new_output="already consumed\n",
            log_cursor="17",
        )
    )
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=ExecuteResult(output="", exit_code=0))
    persisted_cursors: list[tuple[str, str]] = []

    async def _persist_cursor(command_id: str, cursor: str) -> None:
        persisted_cursors.append((command_id, cursor))

    tool = _make_execute_tool(
        sandbox,
        live={},
        persist_reserve=AsyncMock(return_value=True),
        persist_running=AsyncMock(),
        persist_cursor=_persist_cursor,
    )
    result = await tool.execute(
        "tc-cursor",
        _ExecuteArgs(command="long command", description="Long command"),
    )

    assert isinstance(result.details, dict)
    assert persisted_cursors == [(result.details["command_id"], "17")]


@pytest.mark.asyncio
async def test_auto_background_does_not_ack_output_when_log_append_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot
    from cubeplex.sandbox.log_io import AppendOutputResult

    monkeypatch.setattr(sandbox_mod, "AUTO_BACKGROUND_SECONDS", 0)
    monkeypatch.setattr(
        sandbox_mod,
        "append_output",
        AsyncMock(return_value=AppendOutputResult(data_written=False, cleanup_done=True)),
    )
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    sandbox.poll = AsyncMock(
        return_value=ProcessSnapshot(
            status="running",
            new_output="not durable yet\n",
            log_cursor="17",
        )
    )
    persisted_cursors: list[str] = []

    async def _persist_cursor(_command_id: str, cursor: str) -> None:
        persisted_cursors.append(cursor)

    tool = _make_execute_tool(
        sandbox,
        live={},
        persist_reserve=AsyncMock(return_value=True),
        persist_running=AsyncMock(),
        persist_cursor=_persist_cursor,
    )
    await tool.execute(
        "tc-cursor-write-failure",
        _ExecuteArgs(command="long command", description="Long command"),
    )

    assert persisted_cursors == []


@pytest.mark.asyncio
async def test_auto_background_does_not_repeat_streamed_output_during_log_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot
    from cubeplex.sandbox.log_io import AppendOutputResult

    async def _immediate_sleep(_seconds: float) -> None:
        return None

    async def _append(_sandbox: Any, _path: str, data: str | bytes) -> AppendOutputResult:
        return AppendOutputResult(
            data_written=data != "first\n",
            cleanup_done=True,
        )

    monkeypatch.setattr(sandbox_mod.asyncio, "sleep", _immediate_sleep)
    monkeypatch.setattr(sandbox_mod, "append_output", _append)
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    sandbox.poll = AsyncMock(
        side_effect=(
            ProcessSnapshot(status="running", new_output="first\n", log_cursor="1"),
            ProcessSnapshot(
                status="exited",
                exit_code=0,
                new_output="first\nsecond\n",
                log_cursor="2",
            ),
        )
    )

    result = await _make_execute_tool(sandbox).execute(
        "tc-display-cursor",
        _ExecuteArgs(command="long command", description="Long command"),
    )

    assert _text(result) == "first\nsecond\n"


@pytest.mark.asyncio
async def test_background_deadline_persists_final_output_cursor_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.base import ExecuteResult, ProcessHandle, ProcessSnapshot

    async def _immediate_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(sandbox_mod.asyncio, "sleep", _immediate_sleep)
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    sandbox.poll = AsyncMock(
        side_effect=[
            ProcessSnapshot(status="running", new_output="before kill\n", log_cursor="1"),
            ProcessSnapshot(status="killed", new_output="after kill\n", log_cursor="2"),
        ]
    )
    sandbox.kill = AsyncMock()
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=ExecuteResult(output="", exit_code=0))
    cursors: list[str] = []
    timed_out = asyncio.Event()
    timeout_notices: list[bool] = []

    async def _persist_cursor(_command_id: str, cursor: str) -> None:
        cursors.append(cursor)

    async def _persist_timed_out(
        _command_id: str,
        notify: bool,
        _snapshot: ProcessSnapshot,
        _logs_confirmed: bool,
    ) -> None:
        timeout_notices.append(notify)
        timed_out.set()

    tool = _make_execute_tool(
        sandbox,
        live={},
        persist_cursor=_persist_cursor,
        persist_timed_out=_persist_timed_out,
    )
    await tool.execute(
        "tc-deadline-output",
        _ExecuteArgs(
            command="long command",
            description="Long command",
            background=True,
            timeout_seconds=1,
        ),
    )
    await asyncio.wait_for(timed_out.wait(), timeout=1)

    assert cursors == ["1", "2"]
    assert timeout_notices == [True]
    sandbox.kill.assert_awaited_once()


@pytest.mark.asyncio
async def test_background_deadline_keeps_repository_completion_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.base import ExecuteResult, ProcessHandle, ProcessSnapshot

    async def _immediate_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(sandbox_mod.asyncio, "sleep", _immediate_sleep)
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    sandbox.poll = AsyncMock(
        return_value=ProcessSnapshot(status="exited", exit_code=0, log_cursor="1")
    )
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=ExecuteResult(output="", exit_code=0))
    completed = asyncio.Event()
    background_terminal = AsyncMock(side_effect=lambda *_args: completed.set())
    foreground = AsyncMock(side_effect=AssertionError("must not discard repository command"))

    tool = _make_execute_tool(
        sandbox,
        live={},
        persist_reserve=AsyncMock(return_value=True),
        persist_running=AsyncMock(),
        persist_foreground=foreground,
        persist_background_terminal=background_terminal,
    )
    await tool.execute(
        "tc-background-natural-exit",
        _ExecuteArgs(
            command="long command",
            description="Long command",
            background=True,
            timeout_seconds=1,
        ),
    )
    await asyncio.wait_for(completed.wait(), timeout=1)

    background_terminal.assert_awaited_once()
    foreground.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_deadline_preserves_completion_notice_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.base import ExecuteResult, ProcessHandle, ProcessSnapshot

    async def _immediate_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(sandbox_mod.asyncio, "sleep", _immediate_sleep)
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    sandbox.poll = AsyncMock(
        side_effect=[
            ProcessSnapshot(status="running"),
            ProcessSnapshot(status="killed"),
        ]
    )
    sandbox.kill = AsyncMock()
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=ExecuteResult(output="", exit_code=0))
    timeout_notices: list[bool] = []
    persisted = asyncio.Event()

    async def _persist_timed_out(
        _command_id: str,
        notify: bool,
        _snapshot: ProcessSnapshot,
        _logs_confirmed: bool,
    ) -> None:
        timeout_notices.append(notify)
        persisted.set()

    tool = _make_execute_tool(
        sandbox,
        live={},
        persist_timed_out=_persist_timed_out,
    )
    await tool.execute(
        "tc-deadline-no-notice",
        _ExecuteArgs(
            command="long command",
            description="Long command",
            background=True,
            notify_on_complete=False,
            timeout_seconds=1,
        ),
    )
    await asyncio.wait_for(persisted.wait(), timeout=1)

    assert timeout_notices == [False]


@pytest.mark.asyncio
async def test_auto_started_fast_command_spills_oversized_output(tmp_path: Any) -> None:
    from cubeplex.sandbox.local import LocalSandbox

    sandbox = LocalSandbox(workdir=str(tmp_path))
    tool = _make_execute_tool(sandbox)
    result = await tool.execute(
        "tc-auto-spill",
        _ExecuteArgs(
            command="python -c 'print(\"x\" * 25000)'",
            description="Print large output",
        ),
    )
    assert len(_text(result)) <= EXECUTE_RESULT_SPILL_CHARS
    assert "[truncated] full output written to" in _text(result)


@pytest.mark.asyncio
async def test_auto_started_command_reports_external_kill() -> None:
    from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot

    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle("", "p1"))
    sandbox.poll = AsyncMock(return_value=ProcessSnapshot(status="killed"))
    sandbox.upload = AsyncMock()
    sandbox.download = AsyncMock(return_value=[])
    killed: list[str] = []
    discarded: list[str] = []

    async def _killed(command_id: str) -> None:
        killed.append(command_id)

    async def _discard(command_id: str) -> None:
        discarded.append(command_id)

    tool = _make_execute_tool(
        sandbox,
        persist_killed=_killed,
        persist_discard=_discard,
    )
    result = await tool.execute(
        "tc-auto-killed",
        _ExecuteArgs(command="build", description="Build project"),
    )
    assert result.is_error is True
    assert result.details == {"status": "killed"}
    assert "killed by user" in _text(result)
    assert len(killed) == 1
    assert discarded == []


@pytest.mark.asyncio
async def test_monitor_rejects_shell_backgrounding() -> None:
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock()
    tool = _make_monitor_tool(sandbox, live={})
    result = await tool.execute(
        "tc-monitor-bg",
        _MonitorArgs(description="Watch worker", command="worker &"),
    )
    assert result.is_error is True
    assert "monitor tool manages" in _text(result)
    sandbox.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_monitor_hands_off_to_durable_coordinator_immediately() -> None:
    from cubeplex.sandbox.base import ProcessHandle

    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle("", "provider-ref"))
    sandbox.upload = AsyncMock()
    live: dict[str, tuple[ProcessHandle, bool]] = {}
    handed_off: list[str] = []

    async def _handoff(command_id: str) -> bool:
        handed_off.append(command_id)
        live.pop(command_id)
        return True

    tool = _make_monitor_tool(
        sandbox,
        live=live,
        persist_reserve=AsyncMock(return_value=True),
        persist_running=AsyncMock(),
        handoff=_handoff,
    )
    result = await tool.execute(
        "tc-monitor",
        _MonitorArgs(description="Watch worker", command="worker", persistent=True),
    )

    assert isinstance(result.details, dict)
    assert handed_off == [result.details["command_id"]]
    assert live == {}


@pytest.mark.asyncio
async def test_auto_background_skips_when_command_exits_quickly(tmp_path: Any) -> None:
    from cubeplex.sandbox.local import LocalSandbox

    sandbox = LocalSandbox(workdir=str(tmp_path))
    discarded: list[str] = []

    async def _reserve(**kwargs: Any) -> bool:
        del kwargs
        return True

    async def _discard(command_id: str) -> None:
        discarded.append(command_id)

    tool = _make_execute_tool(
        sandbox,
        persist_reserve=_reserve,
        persist_discard=_discard,
    )
    result = await tool.execute(
        "tc-fast",
        _ExecuteArgs(command="echo hello-auto", description="Echo"),
    )
    assert isinstance(result.details, dict)
    assert result.details.get("status") == "exited"
    assert "hello-auto" in _text(result)
    assert len(discarded) == 1


@pytest.mark.asyncio
async def test_bare_sleep_does_not_auto_background() -> None:
    from cubeplex.sandbox.base import ExecuteResult

    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.execute = AsyncMock(return_value=ExecuteResult(output="ok", exit_code=0))
    sandbox.start = AsyncMock()
    tool = _make_execute_tool(sandbox)
    await tool.execute(
        "tc-sleep",
        _ExecuteArgs(command="sleep 30", description="Sleep", timeout_seconds=1),
    )
    sandbox.execute.assert_awaited()
    sandbox.start.assert_not_called()


@pytest.mark.asyncio
async def test_background_log_path_is_written(tmp_path: Any) -> None:
    from cubeplex.sandbox.local import LocalSandbox

    sandbox = LocalSandbox(workdir=str(tmp_path))
    mw = SandboxMiddleware(sandbox=sandbox)
    execute = next(t for t in mw.tools if t.name == "execute")
    result = await execute.execute(
        "tc-log",
        _ExecuteArgs(command="echo hello-bg", description="Echo"),
    )
    assert result.details == {"status": "exited"}
    paths = list((tmp_path / ".cubeplex").glob("execute-*.log"))
    assert len(paths) == 1
    path = paths[0]
    assert path.exists()
    assert "hello-bg" in path.read_text()


@pytest.mark.asyncio
async def test_log_append_does_not_put_large_output_in_shell_command(tmp_path: Any) -> None:
    from cubeplex.sandbox.local import LocalSandbox

    sandbox = LocalSandbox(workdir=str(tmp_path))
    path = tmp_path / ".cubeplex" / "execute-large.log"
    output = "x" * 1_000_000

    await _append_sandbox_log(sandbox, str(path), output)

    assert path.read_text() == output
    assert list((tmp_path / ".cubeplex").glob("*.append-*")) == []


@pytest.mark.asyncio
async def test_on_run_end_does_not_poll_or_kill_unhanded_command() -> None:
    from cubeplex.sandbox.base import ProcessHandle

    sandbox = _make_sandbox()
    sandbox.poll = AsyncMock()
    sandbox.kill = AsyncMock()
    mw = SandboxMiddleware(sandbox=sandbox)
    mw._live_commands["scmd-run"] = (
        ProcessHandle(command_id="scmd-run", provider_ref="provider-1"),
        True,
    )

    notices = await mw.on_run_end(AgentContext(system_prompt="", messages=[]))

    assert notices is None
    sandbox.poll.assert_not_awaited()
    sandbox.kill.assert_not_awaited()
    assert "scmd-run" in mw._live_commands


@pytest.mark.asyncio
async def test_finalize_kills_unreserved_command_without_durable_repository(tmp_path: Any) -> None:
    from cubeplex.sandbox.base import ProcessHandle
    from cubeplex.sandbox.local import LocalSandbox

    sandbox = LocalSandbox(workdir=str(tmp_path))
    mw = SandboxMiddleware(sandbox=sandbox)
    execute = next(t for t in mw.tools if t.name == "execute")
    started = await execute.execute(
        "tc-nf",
        _ExecuteArgs(
            command="sleep 30",
            description="Server",
            background=True,
            notify_on_complete=False,
        ),
    )
    cid = started.details["command_id"]  # type: ignore[index]
    provider_ref = next(iter(sandbox._bg))
    notices = await mw.on_run_end(AgentContext(system_prompt="", messages=[]))
    assert notices is None
    assert cid in mw._live_commands
    await mw.finalize_run()
    assert cid not in mw._live_commands
    handle = ProcessHandle(command_id=str(cid), provider_ref=provider_ref)
    assert (await sandbox.poll(handle)).status == "killed"


@pytest.mark.asyncio
async def test_on_run_end_never_waits_for_unfinished_command() -> None:
    from cubeplex.sandbox.base import ProcessHandle

    sandbox = _make_sandbox()
    sandbox.poll = AsyncMock()
    sandbox.kill = AsyncMock()
    mw = SandboxMiddleware(sandbox=sandbox)
    handle = ProcessHandle(command_id="scmd-run", provider_ref="provider-1")
    mw._live_commands["scmd-run"] = (handle, True)

    notices = await mw.on_run_end(AgentContext(system_prompt="", messages=[]))

    assert notices is None
    sandbox.poll.assert_not_awaited()
    sandbox.kill.assert_not_awaited()
    assert mw._live_commands == {"scmd-run": (handle, True)}


@pytest.mark.asyncio
async def test_monitor_deadline_persists_timeout_after_confirmed_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.base import ExecuteResult, ProcessHandle, ProcessSnapshot

    async def _immediate_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(sandbox_mod.asyncio, "sleep", _immediate_sleep)
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    sandbox.poll = AsyncMock(
        return_value=ProcessSnapshot(status="running", new_output="predicate\n")
    )
    sandbox.kill = AsyncMock()
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=ExecuteResult(output="", exit_code=0))
    persisted = asyncio.Event()

    async def _persist_timeout(_command_id: str) -> None:
        persisted.set()

    tool = _make_monitor_tool(
        sandbox,
        live={},
        persist_monitor_timed_out=_persist_timeout,
    )
    await tool.execute(
        "tc-monitor-timeout",
        _MonitorArgs(
            command="monitor command",
            description="Monitor command",
            timeout_seconds=1,
        ),
    )
    await asyncio.wait_for(persisted.wait(), timeout=1)

    sandbox.kill.assert_awaited_once()


@pytest.mark.asyncio
async def test_monitor_deadline_keeps_repository_terminal_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.base import ExecuteResult, ProcessHandle, ProcessSnapshot
    from cubeplex.sandbox.log_io import AppendOutputResult

    async def _immediate_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(sandbox_mod.asyncio, "sleep", _immediate_sleep)
    monkeypatch.setattr(
        sandbox_mod,
        "append_output",
        AsyncMock(return_value=AppendOutputResult(data_written=False, cleanup_done=True)),
    )
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    sandbox.poll = AsyncMock(
        return_value=ProcessSnapshot(status="exited", exit_code=0, log_cursor="1")
    )
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=ExecuteResult(output="", exit_code=0))
    completed = asyncio.Event()
    terminal = AsyncMock(side_effect=lambda *_args: completed.set())

    tool = _make_monitor_tool(
        sandbox,
        live={},
        persist_reserve=AsyncMock(return_value=True),
        persist_running=AsyncMock(),
        persist_monitor_observation=terminal,
    )
    await tool.execute(
        "tc-monitor-natural-exit",
        _MonitorArgs(
            command="monitor command",
            description="Monitor command",
            timeout_seconds=1,
        ),
    )
    await asyncio.wait_for(completed.wait(), timeout=1)

    terminal.assert_awaited_once()
    assert terminal.await_args.args[2] is False
    sandbox.kill.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_timeout_does_not_confirm_an_unwritten_empty_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.middleware import sandbox as sandbox_mod
    from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot
    from cubeplex.sandbox.log_io import AppendOutputResult

    async def _immediate_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(sandbox_mod.asyncio, "sleep", _immediate_sleep)
    monkeypatch.setattr(
        sandbox_mod,
        "append_output",
        AsyncMock(return_value=AppendOutputResult(data_written=False, cleanup_done=True)),
    )
    sandbox = _make_sandbox()
    sandbox.supports_background = MagicMock(return_value=True)
    sandbox.start = AsyncMock(return_value=ProcessHandle(command_id="", provider_ref="p1"))
    sandbox.poll = AsyncMock(
        side_effect=(
            ProcessSnapshot(status="running"),
            ProcessSnapshot(status="killed"),
        )
    )
    sandbox.kill = AsyncMock()
    persisted = asyncio.Event()
    timeout = AsyncMock(side_effect=lambda *_args: persisted.set())

    tool = _make_monitor_tool(
        sandbox,
        live={},
        persist_monitor_timeout=timeout,
    )
    await tool.execute(
        "tc-monitor-empty-timeout",
        _MonitorArgs(
            command="monitor command",
            description="Monitor command",
            timeout_seconds=1,
        ),
    )
    await asyncio.wait_for(persisted.wait(), timeout=1)

    assert timeout.await_args.args[3] is False


@pytest.mark.asyncio
async def test_finalize_run_kills_only_unreserved_local_commands() -> None:
    from cubeplex.sandbox.base import ProcessHandle

    sandbox = _make_sandbox()
    sandbox.kill = AsyncMock()
    mw = SandboxMiddleware(sandbox=sandbox)
    monitor = ProcessHandle(command_id="scmd-monitor", provider_ref="monitor-ref")
    run_command = ProcessHandle(command_id="scmd-run", provider_ref="run-ref")
    mw._live_commands.update(
        {
            "scmd-monitor": (monitor, False),
            "scmd-run": (run_command, True),
        }
    )
    mw._persist_killed = AsyncMock()
    mw._lease_task = asyncio.create_task(asyncio.Event().wait())

    await mw.finalize_run()

    assert sandbox.kill.await_args_list == [call(monitor), call(run_command)]
    assert mw._persist_killed.await_args_list == [call("scmd-monitor"), call("scmd-run")]
    assert mw._lease_task is None
    assert mw._live_commands == {}


@pytest.mark.asyncio
async def test_managed_finalize_hands_off_without_killing_run_command() -> None:
    from cubeplex.sandbox.base import ProcessHandle

    sandbox = _make_sandbox()
    sandbox.kill = AsyncMock()
    mw = SandboxMiddleware(sandbox=sandbox, session_factory=MagicMock())
    mw._live_commands["scmd-run"] = (
        ProcessHandle(command_id="scmd-run", provider_ref="run-ref"),
        True,
    )
    mw._handoff_conversation_command = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda command_id: (mw._live_commands.pop(command_id), True)[1]
    )

    await mw.finalize_run()

    mw._handoff_conversation_command.assert_awaited_once_with("scmd-run")
    sandbox.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_run_preserves_durable_tracking_when_kill_fails() -> None:
    from cubeplex.sandbox.base import ProcessHandle

    sandbox = _make_sandbox()
    sandbox.kill = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    mw = SandboxMiddleware(sandbox=sandbox)
    mw._live_commands["scmd-run"] = (
        ProcessHandle(command_id="scmd-run", provider_ref="run-ref"),
        True,
    )
    mw._persist_killed = AsyncMock()

    await mw.finalize_run()

    mw._persist_killed.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_persistence_helpers_delegate_with_owner_and_notice_state() -> None:
    from cubeplex.models.sandbox_command import SandboxCommandNoticeState
    from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot

    sandbox = _make_sandbox()
    repo = MagicMock()
    repo.mark_running = AsyncMock(return_value=True)
    repo.update_log_cursor = AsyncMock(return_value=True)
    repo.mark_terminal = AsyncMock(return_value=True)
    row = MagicMock(status="running")
    repo.get = AsyncMock(return_value=row)
    mw = _make_middleware(
        sandbox=sandbox,
        org_id="org-1",
        user_id="user-1",
        run_id="run-1",
        session_factory=MagicMock(),
    )

    @asynccontextmanager
    async def _repo_ctx() -> Any:
        yield repo

    mw._command_repo_ctx = _repo_ctx  # type: ignore[method-assign]
    mw._live_commands["scmd-live"] = (
        ProcessHandle(command_id="scmd-live", provider_ref="provider-1"),
        True,
    )

    await mw._persist_running("scmd-live", "provider-1")
    await mw._persist_cursor("scmd-live", "17")
    assert await mw._persisted_command_status("scmd-live") == "running"
    await mw._persist_terminal(
        "scmd-none",
        status="killed",
        exit_code=None,
        notify=False,
    )
    await mw._persist_terminal(
        "scmd-pending",
        status="exited",
        exit_code=0,
        notify=True,
    )
    await mw._persist_terminal(
        "scmd-delivered",
        status="exited",
        exit_code=0,
        notify=True,
        delivered=True,
    )
    await mw._persist_background_terminal(
        "scmd-killed",
        ProcessSnapshot(status="killed"),
        True,
        True,
    )
    await mw._persist_background_terminal(
        "scmd-unconfirmed",
        ProcessSnapshot(status="exited", exit_code=0),
        False,
        True,
    )
    await mw._renew_live_leases()

    repo.mark_running.assert_awaited_once_with(
        "scmd-live",
        provider_ref="provider-1",
        owner_id="run:run-1",
    )
    repo.update_log_cursor.assert_awaited_once_with(
        "scmd-live",
        log_cursor="17",
        owner_id="run:run-1",
    )
    notices = [call.kwargs["notice_state"] for call in repo.mark_terminal.await_args_list]
    assert notices == [
        SandboxCommandNoticeState.none.value,
        SandboxCommandNoticeState.pending.value,
        SandboxCommandNoticeState.delivered.value,
        SandboxCommandNoticeState.none.value,
    ]


@pytest.mark.asyncio
async def test_repository_monitor_finalization_preserves_the_last_output_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.sandbox import command_coordinator
    from cubeplex.sandbox.base import ProcessSnapshot

    repo = MagicMock()
    repo.get = AsyncMock(return_value=MagicMock())
    repo.session = MagicMock()
    terminalize = AsyncMock(return_value=True)
    monkeypatch.setattr(command_coordinator, "_terminalize", terminalize)
    mw = _make_middleware(session_factory=MagicMock())

    @asynccontextmanager
    async def _repo_ctx() -> Any:
        yield repo

    mw._command_repo_ctx = _repo_ctx  # type: ignore[method-assign]
    await mw._persist_monitor_observation(
        "scmd-monitor",
        ProcessSnapshot(
            status="exited",
            exit_code=0,
            new_output="earlier\nfinal condition matched\n",
        ),
        True,
    )

    assert terminalize.await_args.kwargs["wake_text"] == "final condition matched"
    terminalize.reset_mock()
    await mw._persist_monitor_observation(
        "scmd-monitor",
        ProcessSnapshot(status="exited", exit_code=0, new_output="unconfirmed\n"),
        False,
    )
    terminalize.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["mark_running", "update_log_cursor"])
async def test_command_persistence_cas_miss_is_not_silently_accepted(method: str) -> None:
    repo = MagicMock()
    setattr(repo, method, AsyncMock(return_value=False))
    mw = _make_middleware(
        org_id="org-1",
        user_id="user-1",
        run_id="run-1",
        session_factory=MagicMock(),
    )

    @asynccontextmanager
    async def _repo_ctx() -> Any:
        yield repo

    mw._command_repo_ctx = _repo_ctx  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="cas missed"):
        if method == "mark_running":
            await mw._persist_running("scmd-1", "provider-1")
        else:
            await mw._persist_cursor("scmd-1", "17")


@pytest.mark.asyncio
async def test_persist_reserve_rejects_unadmitted_database_execution() -> None:
    sandbox = _make_sandbox()
    sandbox.user_sandbox_id = "usb-1"
    sandbox.ensure_created = AsyncMock()
    mw = _make_middleware(
        sandbox=sandbox,
        org_id="org-1",
        user_id="user-1",
        run_id="run-1",
        session_factory=MagicMock(),
    )

    with pytest.raises(RuntimeError, match="durable execution admission"):
        await mw._persist_reserve(
            command_id="scmd-1",
            tool_call_id="tc-1",
            command="serve",
            description="Development server",
            notify_on_complete=False,
            log_path="/workspace/server.log",
        )

    sandbox.ensure_created.assert_not_awaited()


@pytest.mark.asyncio
async def test_handoff_releases_owner_and_cancels_local_deadline() -> None:
    from cubeplex.sandbox.base import ProcessHandle

    repo = MagicMock()
    repo.release_owner = AsyncMock()
    mw = _make_middleware(
        org_id="org-1",
        user_id="user-1",
        run_id="run-1",
        session_factory=MagicMock(),
    )

    @asynccontextmanager
    async def _repo_ctx() -> Any:
        yield repo

    mw._command_repo_ctx = _repo_ctx  # type: ignore[method-assign]
    mw._live_commands["scmd-monitor"] = (
        ProcessHandle(command_id="scmd-monitor", provider_ref="provider-1"),
        False,
    )
    deadline = asyncio.create_task(asyncio.Event().wait())
    mw._command_deadline_tasks["scmd-monitor"] = deadline

    assert await mw._handoff_conversation_command("scmd-monitor") is True
    repo.release_owner.assert_awaited_once_with(
        ["scmd-monitor"],
        owner_id="run:run-1",
    )
    assert "scmd-monitor" not in mw._live_commands
    assert deadline.cancelled() or deadline.cancelling()


@pytest.mark.asyncio
async def test_live_task_lease_renews_the_background_task_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.sandbox.base import ProcessHandle
    from cubeplex.services import background_tasks

    session = MagicMock()
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _sessions() -> Any:
        yield session

    service = MagicMock()
    service.renew_owner = AsyncMock()
    monkeypatch.setattr(
        background_tasks,
        "BackgroundTaskService",
        lambda *_args, **_kwargs: service,
    )
    mw = _make_middleware(
        org_id="org-1",
        user_id="user-1",
        run_id="run-1",
        session_factory=_sessions,
    )
    mw._task_bindings["scmd-live"] = _TaskCommandBinding(
        task_id="bgt-live",
        owner_token="owner-live",
        sandbox_instance_id="sandbox-live",
    )
    mw._live_commands["scmd-live"] = (
        ProcessHandle(command_id="scmd-live", provider_ref="provider-live"),
        True,
    )

    await mw._renew_live_leases()

    service.renew_owner.assert_awaited_once()
    assert service.renew_owner.await_args.kwargs["task_id"] == "bgt-live"
    assert service.renew_owner.await_args.kwargs["owner_token"] == "owner-live"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_persisted_command_requires_current_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _make_sandbox()
    sandbox.id = "sandbox-1"
    repo = MagicMock()
    row = MagicMock(
        conversation_id="conv-test",
        user_sandbox_id="usb-1",
        status="running",
        task_id=None,
    )
    repo.get = AsyncMock(return_value=row)
    repo.session.get = AsyncMock(return_value=MagicMock(sandbox_id="sandbox-1"))
    mw = _make_middleware(
        sandbox=sandbox,
        org_id="org-1",
        user_id="user-1",
        run_id="run-1",
        session_factory=MagicMock(),
    )

    @asynccontextmanager
    async def _repo_ctx() -> Any:
        yield repo

    mw._command_repo_ctx = _repo_ctx  # type: ignore[method-assign]
    kill = AsyncMock(return_value=True)
    from cubeplex.sandbox import command_coordinator

    monkeypatch.setattr(command_coordinator, "kill_command", kill)

    assert await mw._kill_persisted_command("scmd-1") is True
    assert kill.await_args.args[:2] == (repo.session, row)

    repo.session.get.return_value = MagicMock(sandbox_id="other-sandbox")
    assert await mw._kill_persisted_command("scmd-1") is False
    assert kill.await_count == 1


@pytest.mark.asyncio
async def test_execute_tool_awaits_async_on_update() -> None:
    """CubeLoop's on_update wraps async emit_event; dropping the coro hides live output."""
    sandbox = _make_sandbox()

    async def _run(
        command: str,
        *,
        timeout: int | None = None,
        on_chunk: Any = None,
        **kwargs: Any,
    ) -> Any:
        del command, timeout, kwargs
        if on_chunk is not None:
            on_chunk("hello")
        result = MagicMock()
        result.output = "hello"
        result.exit_code = 0
        return result

    sandbox.execute = _run
    seen: list[str] = []

    async def _on_update(payload: AgentToolResult) -> None:
        await asyncio.sleep(0)
        seen.append(_text(payload))

    tool = _make_execute_tool(sandbox)
    await tool.execute(
        "tc-async",
        _ExecuteArgs(command="echo hello", description="Echo greeting"),
        on_update=_on_update,
    )
    assert seen == ["hello"]


@pytest.mark.asyncio
async def test_execute_tool_trailing_update_after_throttle() -> None:
    sandbox = _make_sandbox()

    async def _run(
        command: str,
        *,
        timeout: int | None = None,
        on_chunk: Any = None,
        **kwargs: Any,
    ) -> Any:
        del command, timeout, kwargs
        if on_chunk is not None:
            on_chunk("one")
            on_chunk("two")
            await asyncio.sleep(0.15)
        result = MagicMock()
        result.output = "onetwo"
        result.exit_code = 0
        return result

    sandbox.execute = _run
    updates: list[AgentToolResult] = []
    tool = _make_execute_tool(sandbox)
    await tool.execute(
        "tc-trail",
        _ExecuteArgs(command="echo onetwo", description="Echo two chunks"),
        on_update=updates.append,
    )
    assert any("two" in _text(u) for u in updates)


@pytest.mark.asyncio
async def test_execute_tool_live_update_is_capped() -> None:
    sandbox = _make_sandbox()
    huge = "x" * 30_000

    async def _run(
        command: str,
        *,
        timeout: int | None = None,
        on_chunk: Any = None,
        **kwargs: Any,
    ) -> Any:
        del command, timeout, kwargs
        if on_chunk is not None:
            on_chunk(huge)
        result = MagicMock()
        result.output = huge
        result.exit_code = 0
        return result

    sandbox.execute = _run
    sandbox.upload = AsyncMock()
    updates: list[AgentToolResult] = []
    tool = _make_execute_tool(sandbox)
    result = await tool.execute(
        "tc-cap",
        _ExecuteArgs(command="cat big.log", description="Dump a large log"),
        on_update=updates.append,
    )
    assert updates
    assert all(len(_text(u)) <= EXECUTE_RESULT_SPILL_CHARS for u in updates)
    text = _text(result)
    assert text.startswith("x")
    assert "omitted" in text
    assert "[truncated]" in text


@pytest.mark.asyncio
async def test_execute_tool_spills_oversized_output_to_sandbox_file() -> None:
    sandbox = _make_sandbox()
    sandbox.workdir = "/workspace"
    sandbox.upload = AsyncMock()
    huge = "x" * 20_001
    exec_result = MagicMock()
    exec_result.output = huge
    exec_result.exit_code = 0
    sandbox.execute = AsyncMock(return_value=exec_result)

    tool = _make_execute_tool(sandbox)
    result = await tool.execute(
        "tc-huge",
        _ExecuteArgs(command="cat big.log", description="Dump a large log"),
    )
    text = _text(result)
    assert "[truncated]" in text
    assert "/workspace/.cubeplex/execute-tc-huge.log" in text
    sandbox.upload.assert_awaited_once()
    path, content = sandbox.upload.await_args.args[0][0]
    assert path.endswith("execute-tc-huge.log")
    assert content == huge.encode()
    assert len(text) <= EXECUTE_RESULT_SPILL_CHARS


@pytest.mark.asyncio
async def test_execute_spill_survives_tool_result_limit_middleware() -> None:
    sandbox = _make_sandbox()
    sandbox.workdir = "/workspace"
    sandbox.upload = AsyncMock()
    huge = "H" * 12_000 + "T" * 12_000
    exec_result = MagicMock()
    exec_result.output = huge
    exec_result.exit_code = 0
    sandbox.execute = AsyncMock(return_value=exec_result)

    tool = _make_execute_tool(sandbox)
    result = await tool.execute(
        "tc-pipe",
        _ExecuteArgs(command="cat big.log", description="Dump a large log"),
    )
    limit = ToolResultLimitMiddleware(exclude_tool_names={"load_skill"})
    composed = compose_after_tool_call([limit])
    assert composed is not None
    ctx = AfterToolCallContext(
        assistant_message=AssistantMessage(
            content=[ToolCall(id="tc-pipe", name="execute", arguments={})]
        ),
        tool_call=ToolCall(id="tc-pipe", name="execute", arguments={}),
        args={},
        result=result,
        is_error=False,
        context=AgentContext(system_prompt="", messages=[]),
    )
    out = await composed(ctx)
    text = _text(result)
    assert "T" * 20 in text
    assert "execute-tc-pipe.log" in text
    assert len(text) <= EXECUTE_RESULT_SPILL_CHARS
    assert out is None


# ---------------------------------------------------------------------------
# write_file tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file_uploads_to_sandbox() -> None:
    sandbox = _make_sandbox()
    sandbox.upload = AsyncMock()
    check_result = MagicMock()
    check_result.output = "MISSING"
    sandbox.execute = AsyncMock(return_value=check_result)

    tool = _make_write_file_tool(sandbox)
    args = _WriteFileArgs(file_path="/work/hello.txt", content="Hello!")
    result = await tool.execute("tc-1", args)

    sandbox.upload.assert_called_once_with([("/work/hello.txt", b"Hello!")])
    assert "Successfully wrote" in _text(result)
    assert "/work/hello.txt" in _text(result)


# ---------------------------------------------------------------------------
# edit_file tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_file_replaces_unique_string() -> None:
    sandbox = _make_sandbox()
    original = "line1\nfoo bar\nline3"
    sandbox.download = AsyncMock(return_value=[("/work/f.txt", original.encode())])
    sandbox.upload = AsyncMock()

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(file_path="/work/f.txt", old_string="foo bar", new_string="baz qux")
    result = await tool.execute("tc-1", args)

    assert "Successfully edited" in _text(result)
    sandbox.upload.assert_called_once_with([("/work/f.txt", b"line1\nbaz qux\nline3")])


@pytest.mark.asyncio
async def test_edit_file_same_strings_returns_error() -> None:
    sandbox = _make_sandbox()
    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(file_path="/work/f.txt", old_string="same", new_string="same")
    result = await tool.execute("tc-1", args)
    assert "Error" in _text(result)
    assert "must differ" in _text(result)


@pytest.mark.asyncio
async def test_edit_file_not_found_returns_error() -> None:
    sandbox = _make_sandbox()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError("no file"))

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(file_path="/work/missing.txt", old_string="x", new_string="y")
    result = await tool.execute("tc-1", args)
    assert "Error" in _text(result)
    assert "not found" in _text(result)


@pytest.mark.asyncio
async def test_edit_file_old_string_not_found_returns_error() -> None:
    sandbox = _make_sandbox()
    sandbox.download = AsyncMock(return_value=[("/work/f.txt", b"hello world")])

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(
        file_path="/work/f.txt", old_string="missing text", new_string="replacement"
    )
    result = await tool.execute("tc-1", args)
    assert "Error" in _text(result)
    assert "not found" in _text(result)


@pytest.mark.asyncio
async def test_edit_file_non_unique_old_string_returns_error() -> None:
    sandbox = _make_sandbox()
    sandbox.download = AsyncMock(return_value=[("/work/f.txt", b"dup dup")])

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(file_path="/work/f.txt", old_string="dup", new_string="rep")
    result = await tool.execute("tc-1", args)
    assert "Error" in _text(result)
    assert "2 times" in _text(result)


@pytest.mark.asyncio
async def test_edit_file_exact_match_returns_diff_in_details() -> None:
    sandbox = _make_sandbox()
    original = "line1\nfoo bar\nline3"
    sandbox.download = AsyncMock(return_value=[("/work/f.txt", original.encode())])
    sandbox.upload = AsyncMock()

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(file_path="/work/f.txt", old_string="foo bar", new_string="baz qux")
    result = await tool.execute("tc-1", args)

    assert "Successfully edited" in _text(result)
    assert result.details is not None
    assert isinstance(result.details, dict)
    assert result.details["unified_diff"].startswith("--- a/")
    assert result.details["fuzzy_matched"] is False


@pytest.mark.asyncio
async def test_edit_file_applies_multiple_edits_with_one_upload() -> None:
    sandbox = _make_sandbox()
    original = "first = 1\nsecond = 2\nthird = 3\n"
    sandbox.download = AsyncMock(return_value=[("/work/f.py", original.encode())])
    sandbox.upload = AsyncMock()

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(
        file_path="/work/f.py",
        edits=[
            _EditSpec(old_string="first = 1", new_string="first = 10"),
            _EditSpec(old_string="third = 3", new_string="third = 30"),
        ],
    )
    result = await tool.execute("tc-1", args)

    sandbox.upload.assert_called_once_with(
        [("/work/f.py", b"first = 10\nsecond = 2\nthird = 30\n")]
    )
    assert "2 edits" in _text(result)
    assert result.details is not None
    assert result.details["edit_count"] == 2
    assert result.details["match_mode"] == "exact"


@pytest.mark.asyncio
async def test_edit_file_matches_all_edits_against_original_content() -> None:
    sandbox = _make_sandbox()
    original = "value = 1\n"
    sandbox.download = AsyncMock(return_value=[("/work/f.py", original.encode())])
    sandbox.upload = AsyncMock()

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(
        file_path="/work/f.py",
        edits=[
            _EditSpec(old_string="value", new_string="number"),
            _EditSpec(old_string="1", new_string="2"),
        ],
    )
    result = await tool.execute("tc-1", args)

    assert "Successfully edited" in _text(result)
    sandbox.upload.assert_called_once_with([("/work/f.py", b"number = 2\n")])


@pytest.mark.asyncio
async def test_edit_file_rejects_overlapping_edits_without_upload() -> None:
    sandbox = _make_sandbox()
    sandbox.download = AsyncMock(return_value=[("/work/f.txt", b"abcdef")])
    sandbox.upload = AsyncMock()

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(
        file_path="/work/f.txt",
        edits=[
            _EditSpec(old_string="abc", new_string="x"),
            _EditSpec(old_string="cde", new_string="y"),
        ],
    )
    result = await tool.execute("tc-1", args)

    assert "edit 1 overlaps edit 2" in _text(result)
    sandbox.upload.assert_not_called()


@pytest.mark.asyncio
async def test_edit_file_rejects_later_failed_edit_without_upload() -> None:
    sandbox = _make_sandbox()
    sandbox.download = AsyncMock(return_value=[("/work/f.txt", b"first\nsecond")])
    sandbox.upload = AsyncMock()

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(
        file_path="/work/f.txt",
        edits=[
            _EditSpec(old_string="first", new_string="changed"),
            _EditSpec(old_string="missing", new_string="also changed"),
        ],
    )
    result = await tool.execute("tc-1", args)

    assert "edit 2" in _text(result)
    assert "not found" in _text(result)
    sandbox.upload.assert_not_called()


@pytest.mark.asyncio
async def test_edit_file_preserves_bom_and_crlf() -> None:
    sandbox = _make_sandbox()
    original = "\ufefffirst\r\nsecond\r\n"
    sandbox.download = AsyncMock(return_value=[("/work/f.txt", original.encode())])
    sandbox.upload = AsyncMock()

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(
        file_path="/work/f.txt",
        edits=[_EditSpec(old_string="second", new_string="changed")],
    )
    result = await tool.execute("tc-1", args)

    assert "Successfully edited" in _text(result)
    sandbox.upload.assert_called_once_with([("/work/f.txt", "\ufefffirst\r\nchanged\r\n".encode())])
    assert result.details is not None
    assert result.details["first_changed_line"] == 2


@pytest.mark.asyncio
async def test_edit_file_crlf_to_lf_reports_first_changed_line() -> None:
    sandbox = _make_sandbox()
    original = "first\r\nsecond\r\n"
    sandbox.download = AsyncMock(return_value=[("/work/f.txt", original.encode())])
    sandbox.upload = AsyncMock()

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(
        file_path="/work/f.txt",
        edits=[_EditSpec(old_string="second\r\n", new_string="second\n")],
    )
    result = await tool.execute("tc-1", args)

    assert "Successfully edited" in _text(result)
    sandbox.upload.assert_called_once_with([("/work/f.txt", b"first\r\nsecond\n")])
    assert result.details is not None
    assert result.details["first_changed_line"] == 2


def test_first_changed_line_detects_newline_style_only_change() -> None:
    assert _first_changed_line("second\r\n", "second\n") == 1
    assert _first_changed_line("foo", "foo\n") == 1
    assert _first_changed_line("foo\n", "foo") == 1
    assert _first_changed_line("a\nb", "a\nb\n") == 2
    assert _first_changed_line("same\n", "same\n") is None


# ---------------------------------------------------------------------------
# _normalize_for_fuzzy


def test_normalize_for_fuzzy_smart_single_quotes() -> None:
    assert _normalize_for_fuzzy("‘hello’") == "'hello'"


def test_normalize_for_fuzzy_smart_double_quotes() -> None:
    assert _normalize_for_fuzzy("“hello”") == '"hello"'


def test_normalize_for_fuzzy_en_dash() -> None:
    assert _normalize_for_fuzzy("a–b") == "a-b"


def test_normalize_for_fuzzy_nbsp() -> None:
    assert _normalize_for_fuzzy("a b") == "a b"


def test_normalize_for_fuzzy_strips_trailing_whitespace() -> None:
    assert _normalize_for_fuzzy("hello   \nworld  ") == "hello\nworld"


# ---------------------------------------------------------------------------
# edit_file fuzzy matching


@pytest.mark.asyncio
async def test_edit_file_fuzzy_smart_quotes() -> None:
    sandbox = _make_sandbox()
    # File uses ASCII quotes; LLM sends smart quotes in old_string
    original = "print('hello')"
    sandbox.download = AsyncMock(return_value=[("/work/f.py", original.encode())])
    sandbox.upload = AsyncMock()

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(
        file_path="/work/f.py",
        old_string="print(‘hello’)",  # smart single quotes
        new_string="print('world')",
    )
    result = await tool.execute("tc-1", args)

    assert "Successfully edited" in _text(result)
    sandbox.upload.assert_called_once_with([("/work/f.py", b"print('world')")])
    assert result.details is not None
    assert isinstance(result.details, dict)
    assert result.details["fuzzy_matched"] is True
    assert result.details["unified_diff"].startswith("--- a/")


@pytest.mark.asyncio
async def test_edit_file_fuzzy_nbsp() -> None:
    sandbox = _make_sandbox()
    original = "hello world"
    sandbox.download = AsyncMock(return_value=[("/work/f.txt", original.encode())])
    sandbox.upload = AsyncMock()

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(
        file_path="/work/f.txt",
        old_string="hello world",  # NBSP
        new_string="goodbye world",
    )
    result = await tool.execute("tc-1", args)

    assert "Successfully edited" in _text(result)
    sandbox.upload.assert_called_once_with([("/work/f.txt", b"goodbye world")])
    assert result.details is not None
    assert isinstance(result.details, dict)
    assert result.details["fuzzy_matched"] is True


@pytest.mark.asyncio
async def test_edit_file_fuzzy_no_match_returns_error() -> None:
    sandbox = _make_sandbox()
    sandbox.download = AsyncMock(return_value=[("/work/f.txt", b"hello world")])

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(
        file_path="/work/f.txt",
        old_string="completely absent text",
        new_string="replacement",
    )
    result = await tool.execute("tc-1", args)
    assert "Error" in _text(result)
    assert "not found" in _text(result)


@pytest.mark.asyncio
async def test_edit_file_fuzzy_ambiguous_returns_error() -> None:
    sandbox = _make_sandbox()
    # Two lines both using smart quotes -> both normalize to same ASCII form -> ambiguous
    original = "print(‘dup’)\nprint(‘dup’)"
    sandbox.download = AsyncMock(return_value=[("/work/f.txt", original.encode())])

    tool = _make_edit_file_tool(sandbox)
    args = _EditFileArgs(
        file_path="/work/f.txt",
        old_string="print('dup')",  # ASCII quotes: matches both lines after normalization
        new_string="print('rep')",
    )
    result = await tool.execute("tc-1", args)
    assert "Error" in _text(result)
    assert "2 times" in _text(result)


# ---------------------------------------------------------------------------
# file_read tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_read_delegates_to_sandbox() -> None:
    sandbox = _make_sandbox()
    fake_parse_result = MagicMock()
    fake_parse_result.model_dump.return_value = {
        "kind": "text",
        "content": "file contents here",
        "mime": "text/plain",
        "size_bytes": 18,
        "truncated": False,
        "metadata": {},
    }
    sandbox.file_read = AsyncMock(return_value=fake_parse_result)

    tool = _make_file_read_tool(sandbox, conversation_id="conv-1")
    args = _FileReadArgs(path="/work/readme.txt")
    result = await tool.execute("tc-1", args)

    sandbox.file_read.assert_called_once()
    call_kwargs = sandbox.file_read.call_args
    assert call_kwargs[0][0] == "/work/readme.txt"

    payload = json.loads(_text(result))
    assert payload["kind"] == "text"
    assert payload["content"] == "file contents here"


@pytest.mark.asyncio
async def test_file_read_returns_error_kind_when_file_missing() -> None:
    """FileNotFoundError must surface as ErrorOutput, not cubeloop's generic
    str(exc) wrapper (which would leak the bare path as 'file contents').
    """
    sandbox = _make_sandbox()
    sandbox.file_read = AsyncMock(side_effect=FileNotFoundError("/work/missing.xlsx"))

    tool = _make_file_read_tool(sandbox, conversation_id="conv-1")
    args = _FileReadArgs(path="/work/missing.xlsx")
    result = await tool.execute("tc-1", args)

    payload = json.loads(_text(result))
    assert payload["kind"] == "error"
    assert payload["path"] == "/work/missing.xlsx"
    assert "file not found" in payload["error"]
    assert payload["retryable"] is False


@pytest.mark.asyncio
async def test_file_read_returns_error_kind_on_sandbox_error() -> None:
    """SandboxError (provider down etc.) also surfaces as ErrorOutput."""
    from cubeplex.sandbox.base import SandboxError

    sandbox = _make_sandbox()
    sandbox.file_read = AsyncMock(side_effect=SandboxError("provider unreachable"))

    tool = _make_file_read_tool(sandbox, conversation_id="conv-1")
    args = _FileReadArgs(path="/work/data.csv")
    result = await tool.execute("tc-1", args)

    payload = json.loads(_text(result))
    assert payload["kind"] == "error"
    assert payload["path"] == "/work/data.csv"
    assert "sandbox error" in payload["error"]
    assert "provider unreachable" in payload["error"]


@pytest.mark.asyncio
async def test_file_read_returns_error_kind_on_transport_error() -> None:
    """Transient transport errors (httpx etc.) must also surface as ErrorOutput
    with retryable=True — otherwise cubeloop's str(exc) wrapper leaks raw error
    text as tool content, the same trap as the bare FileNotFoundError case.
    """
    sandbox = _make_sandbox()
    sandbox.file_read = AsyncMock(side_effect=ConnectionError("rustfs timed out"))

    tool = _make_file_read_tool(sandbox, conversation_id="conv-1")
    args = _FileReadArgs(path="/work/large.pdf")
    result = await tool.execute("tc-1", args)

    payload = json.loads(_text(result))
    assert payload["kind"] == "error"
    assert payload["retryable"] is True
    assert "rustfs timed out" in payload["error"]


@pytest.mark.asyncio
async def test_file_read_does_not_swallow_cancelled() -> None:
    """The catch-all must let asyncio.CancelledError propagate so user steer /
    abort works. CancelledError is BaseException, not Exception, so the
    ``except Exception`` does not catch it — pin that contract.
    """
    sandbox = _make_sandbox()
    sandbox.file_read = AsyncMock(side_effect=asyncio.CancelledError())

    tool = _make_file_read_tool(sandbox, conversation_id="conv-1")
    args = _FileReadArgs(path="/work/data.csv")
    with pytest.raises(asyncio.CancelledError):
        await tool.execute("tc-1", args)


@pytest.mark.asyncio
async def test_file_read_passes_page_and_line_ranges() -> None:
    from cubeplex.parsers import ParseOptions

    sandbox = _make_sandbox()
    fake_parse_result = MagicMock()
    fake_parse_result.model_dump.return_value = {"kind": "text", "content": "chunk"}
    sandbox.file_read = AsyncMock(return_value=fake_parse_result)

    tool = _make_file_read_tool(sandbox, conversation_id="conv-1")
    args = _FileReadArgs(path="/work/data.pdf", page_range="1-5", line_range=None)
    await tool.execute("tc-1", args)

    _, call_kwargs = sandbox.file_read.call_args
    options: ParseOptions = call_kwargs["options"]
    assert options.page_range == "1-5"
    assert options.line_range is None


# ---------------------------------------------------------------------------
# Constructor without optional args
# ---------------------------------------------------------------------------


def test_constructor_without_optional_ids() -> None:
    """SandboxMiddleware with only sandbox= should work fine."""
    sandbox = _make_sandbox()
    mw = SandboxMiddleware(sandbox=sandbox)
    assert len(mw.tools) == 6
    names = {t.name for t in mw.tools}
    assert names == _EXPECTED_TOOL_NAMES
