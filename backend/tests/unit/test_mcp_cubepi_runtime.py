"""MCP runtime_pi tests (M2.4)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from cubebox.mcp.cubepi_discovery import CubepiMCPServerSpec
from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi


@pytest.mark.asyncio
async def test_load_returns_empty_when_no_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    """When discovery returns [], load returns []."""

    async def _fake_discover(**kw: object) -> list[CubepiMCPServerSpec]:
        return []

    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
        _fake_discover,
    )
    tools = await load_workspace_mcp_tools_for_cubepi(
        session=None,  # type: ignore[arg-type]
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=None,  # type: ignore[arg-type]
        signer=None,  # type: ignore[arg-type]
    )
    assert tools == []


@pytest.mark.asyncio
async def test_load_skips_failing_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that raises during load_mcp_tools_http doesn't kill the whole loader."""
    specs = [
        CubepiMCPServerSpec(server_id="s1", server_name="good", url="http://good", headers={}),
        CubepiMCPServerSpec(server_id="s2", server_name="bad", url="http://bad", headers={}),
    ]

    async def _fake_discover(**kw: object) -> list[CubepiMCPServerSpec]:
        return specs

    fake_tool = type("T", (), {"name": "good_tool"})()

    async def _fake_loader(
        url: str,
        *,
        headers: dict[str, str] | None,
        timeout: float,
    ) -> list[object]:
        if url == "http://bad":
            raise RuntimeError("nope")
        return [fake_tool]

    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
        _fake_discover,
    )
    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
        _fake_loader,
    )

    tools = await load_workspace_mcp_tools_for_cubepi(
        session=None,  # type: ignore[arg-type]
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=None,  # type: ignore[arg-type]
        signer=None,  # type: ignore[arg-type]
    )
    assert len(tools) == 1
    assert getattr(tools[0], "name", None) == "good_tool"


@pytest.mark.asyncio
async def test_load_aggregates_tools_from_multiple_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tools from multiple successful servers are concatenated."""
    specs = [
        CubepiMCPServerSpec(server_id="s1", server_name="srv1", url="http://srv1", headers={}),
        CubepiMCPServerSpec(server_id="s2", server_name="srv2", url="http://srv2", headers={}),
    ]

    async def _fake_discover(**kw: object) -> list[CubepiMCPServerSpec]:
        return specs

    def _make_tool(name: str) -> object:
        return type("T", (), {"name": name})()

    async def _fake_loader(
        url: str,
        *,
        headers: dict[str, str] | None,
        timeout: float,
    ) -> list[object]:
        if url == "http://srv1":
            return [_make_tool("tool_a"), _make_tool("tool_b")]
        return [_make_tool("tool_c")]

    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
        _fake_discover,
    )
    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
        _fake_loader,
    )

    tools = await load_workspace_mcp_tools_for_cubepi(
        session=None,  # type: ignore[arg-type]
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=None,  # type: ignore[arg-type]
        signer=None,  # type: ignore[arg-type]
    )
    assert len(tools) == 3
    names = {getattr(t, "name", None) for t in tools}
    assert names == {"tool_a", "tool_b", "tool_c"}


@pytest.mark.asyncio
async def test_load_all_servers_fail_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """When all servers fail to load, an empty list is returned rather than raising."""
    specs = [
        CubepiMCPServerSpec(server_id="s1", server_name="bad1", url="http://bad1", headers={}),
        CubepiMCPServerSpec(server_id="s2", server_name="bad2", url="http://bad2", headers={}),
    ]

    async def _fake_discover(**kw: object) -> list[CubepiMCPServerSpec]:
        return specs

    async def _fake_loader(
        url: str,
        *,
        headers: dict[str, str] | None,
        timeout: float,
    ) -> list[object]:
        raise ConnectionError("unreachable")

    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
        _fake_discover,
    )
    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
        _fake_loader,
    )

    tools = await load_workspace_mcp_tools_for_cubepi(
        session=None,  # type: ignore[arg-type]
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=None,  # type: ignore[arg-type]
        signer=None,  # type: ignore[arg-type]
    )
    assert tools == []


@pytest.mark.asyncio
async def test_credential_scope_none_attaches_signed_identity_token() -> None:
    """credential_scope=='none' servers get a signed cubebox identity token as Bearer auth."""
    from datetime import timedelta

    from cubebox.mcp.cubepi_discovery import _resolve_token_for_cubepi

    fake_signer = AsyncMock()
    fake_signer.sign = AsyncMock(return_value="fake-token")

    token = await _resolve_token_for_cubepi(
        server_id="srv-1",
        server_name="passthrough",
        server_org_id="org-1",
        auth_method="none",
        credential_scope="none",
        credential_id=None,
        workspace_id="ws-1",
        user_id="user-1",
        cred_service=None,  # type: ignore[arg-type]
        ws_cred_repo=None,  # type: ignore[arg-type]
        user_cred_repo=None,  # type: ignore[arg-type]
        signer=fake_signer,
    )

    assert token == "fake-token"
    fake_signer.sign.assert_awaited_once_with(
        user_id="user-1",
        org_id="org-1",
        workspace_id="ws-1",
        mcp_server_id="srv-1",
        ttl=timedelta(minutes=5),
    )
