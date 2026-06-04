"""Unit tests for SandboxMiddleware (M3.c.1)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent

from cubebox.middleware.sandbox import (
    SandboxMiddleware,
    _EditFileArgs,
    _ExecuteArgs,
    _FileReadArgs,
    _make_edit_file_tool,
    _make_execute_tool,
    _make_file_read_tool,
    _make_write_file_tool,
    _WriteFileArgs,
)
from cubebox.prompts.sandbox import SANDBOX_PROMPT_TEMPLATE

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_TOOL_NAMES = {"execute", "write_file", "edit_file", "file_read"}


def _make_sandbox(workdir: str = "/sandbox/work") -> MagicMock:
    """Return a minimal sandbox mock."""
    sandbox = MagicMock()
    sandbox.workdir = workdir
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
    args = _ExecuteArgs(command="echo hello world")
    result = await tool.execute("tc-1", args, signal=None, on_update=None)

    sandbox.execute.assert_called_once_with("echo hello world")
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
    args = _ExecuteArgs(command="nonexistent")
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
    args = _ExecuteArgs(command="true")
    result = await tool.execute("tc-3", args)

    assert "[exit code:" not in _text(result)


# ---------------------------------------------------------------------------
# write_file tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file_uploads_to_sandbox() -> None:
    sandbox = _make_sandbox()
    sandbox.upload = AsyncMock()

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
async def test_file_read_passes_page_and_line_ranges() -> None:
    from cubebox.parsers import ParseOptions

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
    assert len(mw.tools) == 4
    names = {t.name for t in mw.tools}
    assert names == _EXPECTED_TOOL_NAMES
