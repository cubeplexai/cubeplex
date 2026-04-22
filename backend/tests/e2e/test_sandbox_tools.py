"""E2E test: sandbox middleware tool registration."""

import pytest

from cubebox.middleware.sandbox import SandboxMiddleware
from cubebox.sandbox.local import LocalSandbox

pytestmark = pytest.mark.e2e


def test_sandbox_middleware_registers_file_tools() -> None:
    sandbox = LocalSandbox(workdir="/tmp")
    mw = SandboxMiddleware(sandbox=sandbox)
    names = [t.name for t in mw.tools]
    assert "execute" in names
    assert "write_file" in names
    assert "edit_file" in names


@pytest.mark.asyncio
async def test_write_file_creates_file(tmp_path) -> None:
    sandbox = LocalSandbox(workdir=str(tmp_path))
    mw = SandboxMiddleware(sandbox=sandbox)
    write_tool = next(t for t in mw.tools if t.name == "write_file")
    result = await write_tool.ainvoke(
        {"file_path": str(tmp_path / "hello.txt"), "content": "hello world"}
    )
    assert "hello.txt" in result
    assert (tmp_path / "hello.txt").read_text() == "hello world"


@pytest.mark.asyncio
async def test_edit_file_replaces_content(tmp_path) -> None:
    target = tmp_path / "greet.txt"
    target.write_text("hello world")
    sandbox = LocalSandbox(workdir=str(tmp_path))
    mw = SandboxMiddleware(sandbox=sandbox)
    edit_tool = next(t for t in mw.tools if t.name == "edit_file")
    result = await edit_tool.ainvoke(
        {
            "file_path": str(target),
            "old_string": "hello",
            "new_string": "goodbye",
        }
    )
    assert "greet.txt" in result
    assert target.read_text() == "goodbye world"


@pytest.mark.asyncio
async def test_edit_file_returns_error_for_missing_file(tmp_path) -> None:
    sandbox = LocalSandbox(workdir=str(tmp_path))
    mw = SandboxMiddleware(sandbox=sandbox)
    edit_tool = next(t for t in mw.tools if t.name == "edit_file")
    result = await edit_tool.ainvoke(
        {
            "file_path": str(tmp_path / "nonexistent.txt"),
            "old_string": "hello",
            "new_string": "goodbye",
        }
    )
    assert "Error" in result or "error" in result.lower()
