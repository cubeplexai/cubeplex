import pytest
from cubebox.middleware.sandbox import SandboxMiddleware
from cubebox.sandbox.local import LocalSandbox


def test_sandbox_middleware_registers_execute_tool():
    sandbox = LocalSandbox()
    mw = SandboxMiddleware(sandbox=sandbox)
    tool_names = [t.name for t in mw.tools]
    assert "execute" in tool_names
    assert len(mw.tools) == 1  # only execute, nothing else


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
