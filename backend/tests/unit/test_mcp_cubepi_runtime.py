"""MCP runtime_pi tests (M2.4)."""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock

import pytest

from cubebox.mcp.cubepi_discovery import CubepiMCPServerSpec
from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi


@dataclasses.dataclass
class _FakeTool:
    """Minimal dataclass-compatible stand-in for cubepi.AgentTool in unit tests."""

    name: str


@pytest.mark.asyncio
async def test_load_returns_empty_when_no_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    """When discovery returns [], load returns []."""

    async def _fake_discover(**kw: object) -> list[CubepiMCPServerSpec]:
        return []

    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
        _fake_discover,
    )
    tools, _citation_configs = await load_workspace_mcp_tools_for_cubepi(
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

    fake_tool = _FakeTool(name="good_tool")

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

    tools, _citation_configs = await load_workspace_mcp_tools_for_cubepi(
        session=None,  # type: ignore[arg-type]
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=None,  # type: ignore[arg-type]
        signer=None,  # type: ignore[arg-type]
    )
    assert len(tools) == 1
    # Tools are namespaced as "{server_name}__{bare_name}" after Task 6.
    assert getattr(tools[0], "name", None) == "good__good_tool"


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
        return _FakeTool(name=name)

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

    tools, _citation_configs = await load_workspace_mcp_tools_for_cubepi(
        session=None,  # type: ignore[arg-type]
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=None,  # type: ignore[arg-type]
        signer=None,  # type: ignore[arg-type]
    )
    assert len(tools) == 3
    # Tools are namespaced as "{server_name}__{bare_name}" after Task 6.
    names = {getattr(t, "name", None) for t in tools}
    assert names == {"srv1__tool_a", "srv1__tool_b", "srv2__tool_c"}


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

    tools, _citation_configs = await load_workspace_mcp_tools_for_cubepi(
        session=None,  # type: ignore[arg-type]
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=None,  # type: ignore[arg-type]
        signer=None,  # type: ignore[arg-type]
    )
    assert tools == []


@pytest.mark.asyncio
async def test_effective_credential_mode_workspace_override_wins() -> None:
    """An enabled workspace override with a credential_mode beats the server default."""
    from cubebox.mcp.cubepi_discovery import _effective_credential_mode

    fake_server = type(
        "S",
        (),
        {
            "id": "srv-1",
            "credential_scope": "org",
            "owner_workspace_id": None,
        },
    )()
    fake_override = type(
        "O",
        (),
        {"enabled": True, "credential_mode": "user"},
    )()

    class _Repo:
        async def get_for_workspace_and_server(
            self, *, workspace_id: str, mcp_server_id: str
        ) -> object:
            assert workspace_id == "ws-1"
            assert mcp_server_id == "srv-1"
            return fake_override

    mode = await _effective_credential_mode(
        server=fake_server,
        workspace_id="ws-1",
        override_repo=_Repo(),  # type: ignore[arg-type]
    )
    assert mode == "user"


@pytest.mark.asyncio
async def test_effective_credential_mode_disabled_override_falls_back() -> None:
    """A disabled override is ignored; server default applies."""
    from cubebox.mcp.cubepi_discovery import _effective_credential_mode

    fake_server = type(
        "S",
        (),
        {
            "id": "srv-1",
            "credential_scope": "org",
            "owner_workspace_id": None,
        },
    )()
    fake_override = type(
        "O",
        (),
        {"enabled": False, "credential_mode": "user"},
    )()

    class _Repo:
        async def get_for_workspace_and_server(
            self, *, workspace_id: str, mcp_server_id: str
        ) -> object:
            return fake_override

    mode = await _effective_credential_mode(
        server=fake_server,
        workspace_id="ws-1",
        override_repo=_Repo(),  # type: ignore[arg-type]
    )
    assert mode == "org"


@pytest.mark.asyncio
async def test_effective_credential_mode_no_override_uses_server_default() -> None:
    from cubebox.mcp.cubepi_discovery import _effective_credential_mode

    fake_server = type(
        "S",
        (),
        {
            "id": "srv-1",
            "credential_scope": "workspace",
            "owner_workspace_id": None,
        },
    )()

    class _Repo:
        async def get_for_workspace_and_server(
            self, *, workspace_id: str, mcp_server_id: str
        ) -> object:
            return None

    mode = await _effective_credential_mode(
        server=fake_server,
        workspace_id="ws-1",
        override_repo=_Repo(),  # type: ignore[arg-type]
    )
    assert mode == "workspace"


@pytest.mark.asyncio
async def test_effective_credential_mode_workspace_owned_ignores_override() -> None:
    """Workspace-owned servers never consult overrides."""
    from cubebox.mcp.cubepi_discovery import _effective_credential_mode

    fake_server = type(
        "S",
        (),
        {
            "id": "srv-1",
            "credential_scope": "workspace",
            "owner_workspace_id": "ws-1",
        },
    )()

    called: list[bool] = []

    class _Repo:
        async def get_for_workspace_and_server(
            self, *, workspace_id: str, mcp_server_id: str
        ) -> object:
            called.append(True)
            return None

    mode = await _effective_credential_mode(
        server=fake_server,
        workspace_id="ws-1",
        override_repo=_Repo(),  # type: ignore[arg-type]
    )
    assert mode == "workspace"
    assert called == [], "override_repo must not be consulted for workspace-owned servers"


@pytest.mark.asyncio
async def test_resolve_token_user_scope_via_override_uses_user_credential() -> None:
    """When the effective scope is 'user', the resolver fetches the user credential."""
    from cubebox.mcp.cubepi_discovery import _resolve_token_for_cubepi

    fake_user_cred = type("UC", (), {"credential_id": "cred-user-42"})()

    class _UserRepo:
        async def get(self, *, user_id: str, mcp_server_id: str) -> object:
            assert user_id == "user-1"
            assert mcp_server_id == "srv-1"
            return fake_user_cred

    fake_cred_service = AsyncMock()
    fake_cred_service.get_decrypted = AsyncMock(return_value="decoded-user-secret")

    fake_signer = AsyncMock()
    fake_signer.sign = AsyncMock(return_value="should-not-be-called")

    token = await _resolve_token_for_cubepi(
        server_id="srv-1",
        server_name="shared-server",
        server_org_id="org-1",
        auth_method="bearer",
        # Server default is 'org' but workspace override flipped it to 'user'.
        effective_scope="user",
        credential_id="cred-org-default",  # would have been used at scope='org'
        workspace_id="ws-1",
        user_id="user-1",
        cred_service=fake_cred_service,
        ws_cred_repo=None,  # type: ignore[arg-type]
        user_cred_repo=_UserRepo(),  # type: ignore[arg-type]
        signer=fake_signer,
    )

    assert token == "decoded-user-secret"
    fake_cred_service.get_decrypted.assert_awaited_once()
    args = fake_cred_service.get_decrypted.await_args
    assert args.kwargs["credential_id"] == "cred-user-42"
    fake_signer.sign.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_token_override_to_none_signs_identity_token() -> None:
    """Even if the server's native scope is 'org', an override to 'none' must sign."""
    from datetime import timedelta

    from cubebox.mcp.cubepi_discovery import _resolve_token_for_cubepi

    fake_signer = AsyncMock()
    fake_signer.sign = AsyncMock(return_value="signed-passthrough-token")

    token = await _resolve_token_for_cubepi(
        server_id="srv-1",
        server_name="shared-server",
        server_org_id="org-1",
        auth_method="bearer",
        effective_scope="none",  # override flipped from 'org' default → 'none'
        credential_id="cred-would-be-org",
        workspace_id="ws-1",
        user_id="user-1",
        cred_service=None,  # type: ignore[arg-type]
        ws_cred_repo=None,  # type: ignore[arg-type]
        user_cred_repo=None,  # type: ignore[arg-type]
        signer=fake_signer,
    )

    assert token == "signed-passthrough-token"
    fake_signer.sign.assert_awaited_once_with(
        user_id="user-1",
        org_id="org-1",
        workspace_id="ws-1",
        mcp_server_id="srv-1",
        ttl=timedelta(minutes=5),
    )


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
        effective_scope="none",
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
