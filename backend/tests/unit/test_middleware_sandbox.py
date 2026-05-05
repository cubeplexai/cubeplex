from unittest.mock import AsyncMock, MagicMock

import pytest

from cubebox.middleware.sandbox import SandboxMiddleware, _create_file_read_tool
from cubebox.parsers.schema import TextOutput
from cubebox.sandbox.local import LocalSandbox


def test_sandbox_middleware_registers_execute_tool():
    sandbox = LocalSandbox()
    mw = SandboxMiddleware(sandbox=sandbox)
    tool_names = [t.name for t in mw.tools]
    assert "execute" in tool_names
    assert "write_file" in tool_names
    assert "edit_file" in tool_names
    assert "file_read" in tool_names
    assert len(mw.tools) == 4


@pytest.mark.asyncio
async def test_execute_tool_runs_command():
    sandbox = LocalSandbox()
    mw = SandboxMiddleware(sandbox=sandbox)
    execute_tool = mw.tools[0]
    result = await execute_tool.ainvoke({"command": "echo hello"})
    assert "hello" in result


@pytest.mark.asyncio
async def test_execute_tool_appends_exit_code_on_failure():
    sandbox = LocalSandbox()
    mw = SandboxMiddleware(sandbox=sandbox)
    execute_tool = mw.tools[0]
    result = await execute_tool.ainvoke({"command": "exit 1"})
    assert "exit code: 1" in result


@pytest.mark.asyncio
async def test_file_read_tool_delegates_to_sandbox():
    sandbox = MagicMock()
    sandbox.file_read = AsyncMock(
        return_value=TextOutput(
            path="/tmp/a.txt",
            mime="text/plain",
            content="hi",
            size_bytes=2,
        )
    )
    tool = _create_file_read_tool(sandbox, conversation_id="conv-aaaaaaaaaaaaaa")
    assert tool.name == "file_read"
    result = await tool.ainvoke({"path": "/tmp/a.txt"})
    assert result["content"] == "hi"
    assert result["kind"] == "text"


def test_file_read_tool_description_mentions_use_cases():
    sandbox = MagicMock()
    tool = _create_file_read_tool(sandbox, conversation_id=None)
    desc = tool.description.lower()
    assert "pdf" in desc
    assert "video" in desc or "audio" in desc


def test_sandbox_middleware_preserves_short_prefixed_conversation_id():
    """Regression: middleware previously coerced via UUID(...) and dropped
    short prefixed IDs (e.g. ``conv-V1StGXR8Z5jdHi``) to ``None``, silently
    disabling the file_read dedup cache for every real conversation."""
    sandbox = LocalSandbox()
    cid = "conv-V1StGXR8Z5jdHi"
    mw = SandboxMiddleware(sandbox=sandbox, conversation_id=cid)
    assert mw.conversation_id == cid


def test_sandbox_middleware_handles_none_conversation_id():
    sandbox = LocalSandbox()
    mw = SandboxMiddleware(sandbox=sandbox, conversation_id=None)
    assert mw.conversation_id is None
