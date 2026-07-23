"""Unit tests for the eager sandbox_config tool and middleware registration."""

from __future__ import annotations

import json
from typing import Any

import pytest

from cubeplex.middleware.sandbox import SandboxMiddleware
from cubeplex.services.sandbox_runtime_config import POLICY_DENY_NUDGE
from cubeplex.tools.builtin.sandbox_config import (
    create_sandbox_config_tool,
    make_session_loader,
)


class _FakeSandbox:
    workdir = "/work"


class _ToolCall:
    def __init__(self, name: str, id: str = "call_1") -> None:
        self.name = name
        self.id = id


class _Ctx:
    def __init__(self, name: str, command: str) -> None:
        self.tool_call = _ToolCall(name)
        self.args = {"command": command}


@pytest.mark.asyncio
async def test_sandbox_config_tool_returns_loader_json() -> None:
    calls = 0

    async def loader() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {
            "network": {"default_action": "deny", "rules": []},
            "env": [],
            "command_rules": [],
            "truncated": False,
            "guidance": "g",
        }

    tool = create_sandbox_config_tool(loader)
    assert tool.name == "sandbox_config"
    assert "printenv" in tool.description
    assert "never print secret" in tool.description.lower() or "secret values" in tool.description

    result = await tool.execute("id", tool.parameters(), signal=None, on_update=None)
    assert result.is_error is not True
    text = result.content[0].text  # type: ignore[union-attr]
    data = json.loads(text)
    assert data["network"]["default_action"] == "deny"
    assert calls == 1

    await tool.execute("id2", tool.parameters(), signal=None, on_update=None)
    assert calls == 2


@pytest.mark.asyncio
async def test_sandbox_config_tool_surfaces_loader_errors() -> None:
    async def loader() -> dict[str, Any]:
        raise RuntimeError("db down connection string=secret")

    tool = create_sandbox_config_tool(loader)
    result = await tool.execute("id", tool.parameters(), signal=None, on_update=None)
    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert "temporarily unavailable" in text
    assert "db down" not in text
    assert "secret" not in text


@pytest.mark.asyncio
async def test_make_session_loader_opens_fresh_session_per_call() -> None:
    from contextlib import asynccontextmanager

    sessions_opened = 0

    class _Sess:
        pass

    @asynccontextmanager
    async def factory():
        nonlocal sessions_opened
        sessions_opened += 1
        yield _Sess()

    async def fake_view(session: object, **kwargs: object) -> dict[str, Any]:
        del session, kwargs
        return {"ok": True}

    import cubeplex.tools.builtin.sandbox_config as mod

    orig = mod.load_agent_view
    mod.load_agent_view = fake_view  # type: ignore[assignment]
    try:
        loader = make_session_loader(
            session_factory=factory,
            org_id="o",
            workspace_id="w",
            user_id="u",
            default_image="img",
        )
        assert await loader() == {"ok": True}
        assert await loader() == {"ok": True}
        assert sessions_opened == 2
    finally:
        mod.load_agent_view = orig  # type: ignore[assignment]


def test_middleware_registers_sandbox_config_when_loader_present() -> None:
    async def loader() -> dict[str, Any]:
        return {}

    mw = SandboxMiddleware(
        sandbox=_FakeSandbox(),
        workspace_id="w1",
        config_loader=loader,
    )
    names = {t.name for t in mw.tools}
    assert "sandbox_config" in names
    assert "execute" in names


def test_middleware_omits_sandbox_config_without_loader() -> None:
    mw = SandboxMiddleware(sandbox=_FakeSandbox(), workspace_id="w1")
    names = {t.name for t in mw.tools}
    assert "sandbox_config" not in names


@pytest.mark.asyncio
async def test_policy_deny_reason_includes_sandbox_config_nudge() -> None:
    mw = SandboxMiddleware(
        sandbox=_FakeSandbox(),
        command_rules=[{"action": "deny", "pattern": "rm *"}],
    )
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res is not None and res.block is True
    assert res.hitl_trace["decision"] == "policy_deny"
    assert POLICY_DENY_NUDGE in (res.reason or "")
    assert "command blocked by org policy" in (res.reason or "")


@pytest.mark.asyncio
async def test_ambiguous_allow_path_has_no_false_host_deny() -> None:
    """Allow path: no fabricated host-deny claim, no nudge required."""
    mw = SandboxMiddleware(
        sandbox=_FakeSandbox(),
        command_rules=[{"action": "deny", "pattern": "shutdown"}],
    )
    res = await mw.before_tool_call(_Ctx("execute", "ls -la"), signal=None)
    assert res is None
