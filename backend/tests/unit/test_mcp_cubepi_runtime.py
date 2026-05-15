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
        CubepiMCPServerSpec(
            server_id="s1", server_name="good", url="http://good", transport="sse", headers={}
        ),
        CubepiMCPServerSpec(
            server_id="s2", server_name="bad", url="http://bad", transport="sse", headers={}
        ),
    ]

    async def _fake_discover(**kw: object) -> list[CubepiMCPServerSpec]:
        return specs

    fake_tool = _FakeTool(name="good_tool")

    async def _fake_loader(
        url: str,
        *,
        headers: dict[str, str] | None,
        timeout: float,
        transport: str,
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
        CubepiMCPServerSpec(
            server_id="s1", server_name="srv1", url="http://srv1", transport="sse", headers={}
        ),
        CubepiMCPServerSpec(
            server_id="s2", server_name="srv2", url="http://srv2", transport="sse", headers={}
        ),
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
        transport: str,
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
        CubepiMCPServerSpec(
            server_id="s1", server_name="bad1", url="http://bad1", transport="sse", headers={}
        ),
        CubepiMCPServerSpec(
            server_id="s2", server_name="bad2", url="http://bad2", transport="sse", headers={}
        ),
    ]

    async def _fake_discover(**kw: object) -> list[CubepiMCPServerSpec]:
        return specs

    async def _fake_loader(
        url: str,
        *,
        headers: dict[str, str] | None,
        timeout: float,
        transport: str,
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


# ---------------------------------------------------------------------------
# Four-layer loader (Task 5 Step 4).
#
# Exercises ``load_workspace_mcp_tools_from_effective`` with a stubbed
# :class:`MCPEffectiveConnectorService`. The legacy path keeps its existing
# tests above; these confirm the new path resolves the no-auth identity
# branch and the OAuth-grant branch.
# ---------------------------------------------------------------------------


def _make_runtime_spec(
    *,
    install_id: str = "mcins-1",
    name: str = "demo",
    auth_method: str = "none",
    credential_id: str | None = None,
    grant_scope: str | None = None,
    tool_citations: dict[str, dict[str, object]] | None = None,
):
    """Build an MCPRuntimeConnectorSpec the loader can consume."""
    from cubebox.mcp.effective import MCPRuntimeConnectorSpec

    return MCPRuntimeConnectorSpec(
        install_id=install_id,
        name=name,
        server_url=f"https://mcp.example/{install_id}",
        transport="streamable_http",
        auth_method=auth_method,
        grant_scope=grant_scope,
        credential_id=credential_id,
        refresh_credential_id=None,
        tool_citations=tool_citations or {},
        headers={},
        timeout=30.0,
        sse_read_timeout=300.0,
        template_id=None,
        org_id="org-1",
        workspace_id="ws-1",
    )


class _StubEffectiveService:
    """Drop-in for MCPEffectiveConnectorService.list_runtime_specs in tests."""

    def __init__(self, specs):
        self._specs = specs

    async def list_runtime_specs(self, workspace_id: str, user_id: str):
        assert workspace_id == "ws-1"
        assert user_id == "user-1"
        return self._specs


@pytest.mark.asyncio
async def test_effective_loader_no_auth_signs_identity_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``auth_method='none'`` branch signs a cubebox identity token and uses it."""
    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_from_effective

    spec = _make_runtime_spec(install_id="mcins-passthru", name="srv", auth_method="none")

    captured_headers: dict[str, str] = {}

    async def _fake_loader(
        url: str,
        *,
        headers: dict[str, str] | None,
        timeout: float,
        transport: str,
    ):
        captured_headers.update(headers or {})
        return [_FakeTool(name="ping")]

    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
        _fake_loader,
    )

    fake_signer = AsyncMock()
    fake_signer.sign = AsyncMock(return_value="signed-jwt")

    tools, _ = await load_workspace_mcp_tools_from_effective(
        effective_service=_StubEffectiveService([spec]),  # type: ignore[arg-type]
        token_manager=None,  # type: ignore[arg-type]
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=None,  # type: ignore[arg-type]
        signer=fake_signer,
    )

    assert len(tools) == 1
    assert getattr(tools[0], "name", "") == "srv__ping"
    assert captured_headers.get("Authorization") == "Bearer signed-jwt"
    fake_signer.sign.assert_awaited_once()


@pytest.mark.asyncio
async def test_effective_loader_oauth_reads_grant_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OAuth branch decrypts the access-token credential and injects Bearer auth.

    The four-layer effective service is responsible for filtering expired
    grants; the loader's only job is to translate ``credential_id`` into a
    bearer header via the credential service. (The legacy
    ``OAuthTokenManager.get_access_token`` API takes an MCPServer instance
    that doesn't exist in the four-layer schema; refresh integration is
    queued for Task 9.)
    """
    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_from_effective

    spec = _make_runtime_spec(
        install_id="mcins-oauth",
        name="oauth-srv",
        auth_method="oauth",
        credential_id="cred-access",
        grant_scope="org",
    )

    async def _fake_loader(
        url: str,
        *,
        headers: dict[str, str] | None,
        timeout: float,
        transport: str,
    ):
        assert (headers or {}).get("Authorization") == "Bearer the-access-token"
        return [_FakeTool(name="search")]

    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
        _fake_loader,
    )

    fake_cred_service = AsyncMock()
    fake_cred_service.get_decrypted = AsyncMock(return_value="the-access-token")

    fake_token_manager = AsyncMock()  # exists but is not consulted yet

    tools, _ = await load_workspace_mcp_tools_from_effective(
        effective_service=_StubEffectiveService([spec]),  # type: ignore[arg-type]
        token_manager=fake_token_manager,
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=fake_cred_service,
        signer=AsyncMock(),
    )

    assert len(tools) == 1
    assert getattr(tools[0], "name", "") == "oauth_srv__search"
    fake_cred_service.get_decrypted.assert_awaited_once()
    args = fake_cred_service.get_decrypted.await_args
    assert args.kwargs["credential_id"] == "cred-access"


# ---------------------------------------------------------------------------
# OAuth refresh wiring on the four-layer loader (Codex P2 fix).
#
# When a spec carries a still-live grant (with refresh credential), the loader
# routes through ``OAuthTokenManager.get_access_token_for_grant`` so a
# near-expiry token is rotated before being sent as Bearer. The manager
# decides cached-vs-refresh based on ``grant.expires_at`` — the loader just
# trusts whatever string the manager returns.
# ---------------------------------------------------------------------------


def _make_oauth_runtime_spec(*, grant, oauth_client_config: dict[str, object] | None = None):
    """Build an MCPRuntimeConnectorSpec for an OAuth grant."""
    from cubebox.mcp.effective import MCPRuntimeConnectorSpec

    return MCPRuntimeConnectorSpec(
        install_id="mcins-oauth",
        name="oauth-srv",
        server_url="https://mcp.example/oauth",
        transport="streamable_http",
        auth_method="oauth",
        grant_scope=grant.grant_scope,
        credential_id=grant.credential_id,
        refresh_credential_id=grant.refresh_credential_id,
        tool_citations={},
        headers={},
        timeout=30.0,
        sse_read_timeout=300.0,
        template_id=None,
        org_id="org-1",
        workspace_id="ws-1",
        grant=grant,
        oauth_client_config=oauth_client_config or {"client_id": "client-abc"},
    )


@pytest.mark.asyncio
async def test_effective_loader_oauth_refreshes_via_token_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OAuth grant with a refresh credential routes through the token manager."""
    from datetime import UTC, datetime, timedelta

    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_from_effective
    from cubebox.models import MCPCredentialGrant

    grant = MCPCredentialGrant(
        org_id="org-1",
        install_id="mcins-oauth",
        grant_scope="org",
        workspace_id=None,
        user_id=None,
        credential_id="cred-access",
        refresh_credential_id="cred-refresh",
        expires_at=datetime.now(UTC) - timedelta(seconds=10),
        grant_status="valid",
        created_by_user_id="user-1",
    )
    spec = _make_oauth_runtime_spec(grant=grant)

    captured_auth: dict[str, str] = {}

    async def _fake_loader(
        url: str,
        *,
        headers: dict[str, str] | None,
        timeout: float,
        transport: str,
    ):
        captured_auth["Authorization"] = (headers or {}).get("Authorization", "")
        return [_FakeTool(name="search")]

    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
        _fake_loader,
    )

    fake_cred_service = AsyncMock()
    fake_cred_service.get_decrypted = AsyncMock(return_value="should-not-be-used")

    fake_token_manager = AsyncMock()
    fake_token_manager.get_access_token_for_grant = AsyncMock(return_value="rotated-access")

    fake_grant_repo = AsyncMock()

    tools, _ = await load_workspace_mcp_tools_from_effective(
        effective_service=_StubEffectiveService([spec]),  # type: ignore[arg-type]
        token_manager=fake_token_manager,
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=fake_cred_service,
        signer=AsyncMock(),
        grant_repo=fake_grant_repo,
    )

    assert len(tools) == 1
    assert captured_auth["Authorization"] == "Bearer rotated-access"
    fake_token_manager.get_access_token_for_grant.assert_awaited_once()
    call_kwargs = fake_token_manager.get_access_token_for_grant.await_args.kwargs
    assert call_kwargs["grant"] is grant
    assert call_kwargs["grant_repo"] is fake_grant_repo
    assert call_kwargs["server_url"] == spec.server_url
    assert call_kwargs["oauth_client_config"] == {"client_id": "client-abc"}
    # The cached-credential fall-through must NOT fire when refresh succeeds.
    fake_cred_service.get_decrypted.assert_not_awaited()


@pytest.mark.asyncio
async def test_effective_loader_oauth_no_refresh_cred_falls_back_to_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No refresh credential → loader skips the manager and uses the cached access token.

    Effective-state rule 8 will eventually filter out such grants when they
    expire, but until expiry the cached token is still usable. The runtime
    must not try to refresh a grant that has no refresh credential.
    """
    from datetime import UTC, datetime, timedelta

    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_from_effective
    from cubebox.models import MCPCredentialGrant

    grant = MCPCredentialGrant(
        org_id="org-1",
        install_id="mcins-oauth",
        grant_scope="org",
        workspace_id=None,
        user_id=None,
        credential_id="cred-access",
        refresh_credential_id=None,  # ← no refresh available
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        grant_status="valid",
        created_by_user_id="user-1",
    )
    spec = _make_oauth_runtime_spec(grant=grant)

    captured_auth: dict[str, str] = {}

    async def _fake_loader(
        url: str,
        *,
        headers: dict[str, str] | None,
        timeout: float,
        transport: str,
    ):
        captured_auth["Authorization"] = (headers or {}).get("Authorization", "")
        return [_FakeTool(name="search")]

    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
        _fake_loader,
    )

    fake_cred_service = AsyncMock()
    fake_cred_service.get_decrypted = AsyncMock(return_value="cached-token")
    fake_token_manager = AsyncMock()
    fake_token_manager.get_access_token_for_grant = AsyncMock(return_value="should-not-be-used")

    tools, _ = await load_workspace_mcp_tools_from_effective(
        effective_service=_StubEffectiveService([spec]),  # type: ignore[arg-type]
        token_manager=fake_token_manager,
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=fake_cred_service,
        signer=AsyncMock(),
        grant_repo=AsyncMock(),
    )

    assert len(tools) == 1
    assert captured_auth["Authorization"] == "Bearer cached-token"
    fake_token_manager.get_access_token_for_grant.assert_not_awaited()
    fake_cred_service.get_decrypted.assert_awaited_once()


@pytest.mark.asyncio
async def test_effective_loader_oauth_refresh_failure_falls_back_to_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refresh failure → grant marked expired by the manager; loader uses cached token.

    The manager has already set ``grant.grant_status='expired'`` on the row;
    the next request's effective-state pass will surface ``grant_expired``
    and drop the connector. For this in-flight request we still attempt the
    call with the cached token (the MCP server will 401, the agent moves on).
    """
    from datetime import UTC, datetime, timedelta

    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_from_effective
    from cubebox.mcp.exceptions import OAuthRefreshFailed
    from cubebox.models import MCPCredentialGrant

    grant = MCPCredentialGrant(
        org_id="org-1",
        install_id="mcins-oauth",
        grant_scope="org",
        workspace_id=None,
        user_id=None,
        credential_id="cred-access",
        refresh_credential_id="cred-refresh",
        expires_at=datetime.now(UTC) - timedelta(seconds=10),
        grant_status="valid",
        created_by_user_id="user-1",
    )
    spec = _make_oauth_runtime_spec(grant=grant)

    captured_auth: dict[str, str] = {}

    async def _fake_loader(
        url: str,
        *,
        headers: dict[str, str] | None,
        timeout: float,
        transport: str,
    ):
        captured_auth["Authorization"] = (headers or {}).get("Authorization", "")
        return [_FakeTool(name="search")]

    monkeypatch.setattr(
        "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
        _fake_loader,
    )

    fake_cred_service = AsyncMock()
    fake_cred_service.get_decrypted = AsyncMock(return_value="stale-cached")

    fake_token_manager = AsyncMock()
    fake_token_manager.get_access_token_for_grant = AsyncMock(
        side_effect=OAuthRefreshFailed(401, error="invalid_grant")
    )

    tools, _ = await load_workspace_mcp_tools_from_effective(
        effective_service=_StubEffectiveService([spec]),  # type: ignore[arg-type]
        token_manager=fake_token_manager,
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=fake_cred_service,
        signer=AsyncMock(),
        grant_repo=AsyncMock(),
    )

    # The tool still loads (with the stale token) — the next run will see
    # ``grant_status='expired'`` and drop the connector entirely.
    assert len(tools) == 1
    assert captured_auth["Authorization"] == "Bearer stale-cached"
    fake_token_manager.get_access_token_for_grant.assert_awaited_once()
    fake_cred_service.get_decrypted.assert_awaited_once()
