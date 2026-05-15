"""Unit tests for MCPServerService invariant enforcement."""

from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.auth.context import RequestContext
from cubebox.credentials.encryption import FernetBackend
from cubebox.mcp.exceptions import (
    MCPCredentialPathMismatch,
    MCPCredentialRequired,
    MCPServerAlreadyOrgWide,
    MCPServerNameConflict,
    MCPServerNotFound,
    MCPServerURLConflict,
    MCPUserScopeCredentialForbidden,
)
from cubebox.models import Role, User
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
    WorkspaceMCPOverrideRepository,
)
from cubebox.services.credential import CredentialService
from cubebox.services.mcp import MCPServerService


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db_session:
        yield db_session
    await engine.dispose()


@pytest.fixture
def request_context() -> RequestContext:
    return RequestContext(
        user=User(id="u1", email="u1@example.com", hashed_password="x"),
        org_id="org-test",
        workspace_id="ws-test",
        role=Role.ADMIN,
    )


@pytest.fixture
def encryption_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


@pytest.fixture
def cred_service(
    session: AsyncSession,
    encryption_backend: FernetBackend,
    request_context: RequestContext,
) -> CredentialService:
    repo = CredentialRepository(session, org_id=request_context.org_id)
    return CredentialService(
        repo,
        encryption_backend,
        org_id=request_context.org_id,
        actor_user_id=request_context.user.id,
    )


@pytest.fixture
def mcp_service(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    cred_service: CredentialService,
    request_context: RequestContext,
) -> MCPServerService:
    async def _discover_success(*_args: object, **_kwargs: object) -> tuple[bool, list, None]:
        return True, [], None

    # ``services.mcp`` calls discover_tools directly from ``test_connection``
    # (transient path); the persistent refresh path delegates to
    # ``cubebox.mcp.cubepi_admin_refresh`` which imports
    # ``discover_tools_metadata`` directly. Patch both bindings.
    monkeypatch.setattr("cubebox.services.mcp.discover_tools", _discover_success)
    monkeypatch.setattr(
        "cubebox.mcp.cubepi_admin_refresh.discover_tools_metadata", _discover_success
    )

    return MCPServerService(
        server_repo=MCPServerRepository(session, org_id=request_context.org_id),
        ws_cred_repo=WorkspaceMCPCredentialRepository(session, org_id=request_context.org_id),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=request_context.org_id),
        override_repo=WorkspaceMCPOverrideRepository(session, org_id=request_context.org_id),
        cred_service=cred_service,
        request_context=request_context,
    )


async def test_create_org_scope_requires_credential(mcp_service: MCPServerService) -> None:
    with pytest.raises(MCPCredentialRequired):
        await mcp_service.create(
            name="x",
            server_url="https://a",
            transport="streamable_http",
            auth_method="static",
            credential_scope="org",
            credential_plaintext=None,
        )


async def test_create_user_scope_rejects_credential(mcp_service: MCPServerService) -> None:
    with pytest.raises(MCPUserScopeCredentialForbidden):
        await mcp_service.create(
            name="x",
            server_url="https://a",
            transport="streamable_http",
            auth_method="static",
            credential_scope="user",
            credential_plaintext="should-not-be-here",
        )


async def test_create_oauth_with_plaintext_rejected(mcp_service: MCPServerService) -> None:
    """auth_method=oauth never accepts a static credential at create-time."""
    with pytest.raises(MCPUserScopeCredentialForbidden):
        await mcp_service.create(
            name="x",
            server_url="https://a",
            transport="streamable_http",
            auth_method="oauth",
            credential_scope="org",
            credential_plaintext="x",
        )


async def test_create_oauth_workspace_scope_rejected_before_commit(
    mcp_service: MCPServerService,
) -> None:
    """auth_method=oauth + credential_scope=workspace rejected at validation.

    Before this guard, the workspace credential row was created AFTER the
    server commit, so an OAuth + workspace request raised
    ``MCPCredentialRequired`` only after committing an unauthed server row,
    and the orphan row blocked subsequent retries with the same name/URL.
    """
    with pytest.raises(ValueError, match="credential_scope"):
        await mcp_service.create(
            name="oauth-ws",
            server_url="https://oauth-ws.example.com",
            transport="streamable_http",
            auth_method="oauth",
            credential_scope="workspace",
            credential_plaintext=None,
            owner_workspace_id="ws-test-1234567890",
        )
    # No orphan server row written.
    listed = await mcp_service.server_repo.list_for_org(owner_workspace_id=None)
    assert all(s.name != "oauth-ws" for s in listed)


async def test_create_oauth_org_scope_persists_without_credential(
    mcp_service: MCPServerService,
) -> None:
    """auth_method=oauth + credential_scope=org persists with no credential row.

    The OAuth callback handler is the actual writer; create-time leaves
    ``credential_id=None`` and ``authed=False`` until the dance completes.
    """
    server = await mcp_service.create(
        name="oauth-server",
        server_url="https://oauth.example.com",
        transport="streamable_http",
        auth_method="oauth",
        credential_scope="org",
        credential_plaintext=None,
    )
    assert server.auth_method == "oauth"
    assert server.credential_scope == "org"
    assert server.credential_id is None
    assert server.authed is False


async def test_duplicate_url_in_same_scope_conflicts(mcp_service: MCPServerService) -> None:
    await mcp_service.create(
        name="a",
        server_url="https://x",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    with pytest.raises(MCPServerURLConflict):
        await mcp_service.create(
            name="b",
            server_url="https://x",
            transport="streamable_http",
            auth_method="none",
            credential_scope="none",
        )


async def test_duplicate_name_in_same_scope_conflicts(mcp_service: MCPServerService) -> None:
    await mcp_service.create(
        name="dup",
        server_url="https://a",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    with pytest.raises(MCPServerNameConflict):
        await mcp_service.create(
            name="dup",
            server_url="https://b",
            transport="streamable_http",
            auth_method="none",
            credential_scope="none",
        )


async def test_update_renaming_to_existing_name_conflicts(
    mcp_service: MCPServerService,
) -> None:
    first = await mcp_service.create(
        name="a",
        server_url="https://x",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )
    await mcp_service.create(
        name="b",
        server_url="https://y",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    with pytest.raises(MCPServerNameConflict):
        await mcp_service.update(server_id=first.id, name="b")


async def test_delete_cascades_and_removes_server(mcp_service: MCPServerService) -> None:
    server = await mcp_service.create(
        name="c",
        server_url="https://z",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    await mcp_service.delete(server_id=server.id)

    with pytest.raises(MCPServerNotFound):
        await mcp_service.update(server_id=server.id, name="x")


async def test_refresh_tools_updates_server_auth_state(
    mcp_service: MCPServerService,
) -> None:
    server = await mcp_service.create(
        name="refresh",
        server_url="https://refresh",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    refreshed = await mcp_service.refresh_tools(server_id=server.id)

    assert refreshed.authed is True
    assert refreshed.last_discovered_at is not None


async def test_test_connection_does_not_persist_server(
    mcp_service: MCPServerService,
) -> None:
    success, tools, error = await mcp_service.test_connection(
        server_url="https://dry-run",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    assert success is True
    assert tools == []
    assert error is None
    assert await mcp_service.server_repo.list_for_org() == []


async def test_promote_alpha_moves_workspace_cred_to_inline(
    mcp_service: MCPServerService,
) -> None:
    server = await mcp_service.create(
        name="prom-a",
        server_url="https://p1",
        transport="streamable_http",
        auth_method="static",
        credential_scope="workspace",
        credential_plaintext="ws-key-1",
        owner_workspace_id="ws-test",
    )

    promoted = await mcp_service.promote_to_org(
        server_id=server.id,
        share_credential=True,
    )

    assert promoted.owner_workspace_id is None
    assert promoted.credential_scope == "org"
    assert promoted.credential_id is not None
    assert (
        await mcp_service.ws_cred_repo.get(
            workspace_id="ws-test",
            mcp_server_id=server.id,
        )
        is None
    )
    # Promotion creates an enabled=True override for the source workspace
    # so the promoter still sees the connector (default-invisible semantics).
    override = await mcp_service.override_repo.get_for_workspace_and_server(
        workspace_id="ws-test",
        mcp_server_id=server.id,
    )
    assert override is not None
    assert override.enabled is True


async def test_promote_beta_keeps_workspace_cred(
    mcp_service: MCPServerService,
) -> None:
    server = await mcp_service.create(
        name="prom-b",
        server_url="https://p2",
        transport="streamable_http",
        auth_method="static",
        credential_scope="workspace",
        credential_plaintext="ws-key-2",
        owner_workspace_id="ws-test",
    )

    promoted = await mcp_service.promote_to_org(
        server_id=server.id,
        share_credential=False,
    )

    assert promoted.owner_workspace_id is None
    assert promoted.credential_scope == "workspace"
    ws_credential = await mcp_service.ws_cred_repo.get(
        workspace_id="ws-test",
        mcp_server_id=server.id,
    )
    assert ws_credential is not None
    # Promotion creates an enabled=True override for the source workspace.
    override = await mcp_service.override_repo.get_for_workspace_and_server(
        workspace_id="ws-test",
        mcp_server_id=server.id,
    )
    assert override is not None
    assert override.enabled is True


async def test_promote_already_org_wide_raises(
    mcp_service: MCPServerService,
) -> None:
    server = await mcp_service.create(
        name="prom-c",
        server_url="https://p3",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
    )

    with pytest.raises(MCPServerAlreadyOrgWide):
        await mcp_service.promote_to_org(server_id=server.id, share_credential=False)


async def test_workspace_credential_management_and_overrides(
    mcp_service: MCPServerService,
) -> None:
    server = await mcp_service.create(
        name="ws-cred",
        server_url="https://ws-cred",
        transport="streamable_http",
        auth_method="static",
        credential_scope="workspace",
        credential_plaintext="initial",
        owner_workspace_id="ws-test",
    )
    await mcp_service.promote_to_org(server_id=server.id, share_credential=False)

    credential_id = await mcp_service.set_workspace_credential(
        server_id=server.id,
        workspace_id="ws-2",
        plaintext="ws-secret",
    )
    assert credential_id
    assert (
        await mcp_service.has_workspace_credential(
            server_id=server.id,
            workspace_id="ws-2",
        )
        is True
    )

    # Enable visibility for ws-2 (creates an enabled=True override row).
    await mcp_service.set_workspace_override(
        server_id=server.id,
        workspace_id="ws-2",
        enabled=True,
    )
    override = await mcp_service.override_repo.get_for_workspace_and_server(
        workspace_id="ws-2",
        mcp_server_id=server.id,
    )
    assert override is not None
    assert override.enabled is True

    # Disable removes the override row (no row = invisible).
    await mcp_service.set_workspace_override(
        server_id=server.id,
        workspace_id="ws-2",
        enabled=False,
    )
    cleared = await mcp_service.override_repo.get_for_workspace_and_server(
        workspace_id="ws-2",
        mcp_server_id=server.id,
    )
    assert cleared is None

    await mcp_service.delete_workspace_credential(
        server_id=server.id,
        workspace_id="ws-2",
    )
    assert (
        await mcp_service.has_workspace_credential(
            server_id=server.id,
            workspace_id="ws-2",
        )
        is False
    )


async def test_user_credential_management_rejects_wrong_scope(
    mcp_service: MCPServerService,
) -> None:
    workspace_server = await mcp_service.create(
        name="wrong-user-scope",
        server_url="https://wrong-user-scope",
        transport="streamable_http",
        auth_method="static",
        credential_scope="workspace",
        credential_plaintext="initial",
        owner_workspace_id="ws-test",
    )
    user_server = await mcp_service.create(
        name="user-cred",
        server_url="https://user-cred",
        transport="streamable_http",
        auth_method="static",
        credential_scope="user",
    )

    with pytest.raises(MCPCredentialPathMismatch):
        await mcp_service.set_user_credential(
            server_id=workspace_server.id,
            user_id="u2",
            workspace_id="ws-test",
            plaintext="user-secret",
        )

    credential_id = await mcp_service.set_user_credential(
        server_id=user_server.id,
        user_id="u2",
        workspace_id="ws-test",
        plaintext="user-secret",
    )
    assert credential_id
    assert await mcp_service.has_user_credential(server_id=user_server.id, user_id="u2")
    await mcp_service.delete_user_credential(server_id=user_server.id, user_id="u2")
    assert not await mcp_service.has_user_credential(
        server_id=user_server.id,
        user_id="u2",
    )


async def test_setting_user_credential_discovers_tools(
    mcp_service: MCPServerService,
) -> None:
    server = await mcp_service.create(
        name="user-discovery",
        server_url="https://user-discovery",
        transport="streamable_http",
        auth_method="static",
        credential_scope="user",
    )
    assert server.authed is False

    await mcp_service.set_user_credential(
        server_id=server.id,
        user_id="u2",
        workspace_id="ws-test",
        plaintext="user-secret",
    )

    updated = await mcp_service.server_repo.get(server.id)
    assert updated is not None
    assert updated.authed is True
    assert updated.last_discovered_at is not None


async def test_workspace_override_credential_mode_redirects_credential_writes(
    mcp_service: MCPServerService,
) -> None:
    """A workspace override with credential_mode='workspace' should allow
    set_workspace_credential against an otherwise org-scoped server, and
    credential_mode='user' should allow set_user_credential."""
    server = await mcp_service.create(
        name="org-shared",
        server_url="https://org-shared",
        transport="streamable_http",
        auth_method="static",
        credential_scope="org",
        credential_plaintext="org-secret",
    )

    # Without an override, the workspace path is still gated by server scope.
    with pytest.raises(MCPCredentialPathMismatch):
        await mcp_service.set_workspace_credential(
            server_id=server.id,
            workspace_id="ws-test",
            plaintext="ws-secret",
        )

    # Override declaring credential_mode='workspace' redirects writes there.
    await mcp_service.override_repo.upsert(
        workspace_id="ws-test",
        mcp_server_id=server.id,
        enabled=True,
        updated_by_user_id="u1",
    )
    override = await mcp_service.override_repo.get_for_workspace_and_server(
        workspace_id="ws-test", mcp_server_id=server.id
    )
    assert override is not None
    override.credential_mode = "workspace"
    await mcp_service.override_repo.session.commit()

    cred_id = await mcp_service.set_workspace_credential(
        server_id=server.id,
        workspace_id="ws-test",
        plaintext="ws-secret",
    )
    assert cred_id

    # And credential_mode='user' redirects to per-user credentials similarly.
    override.credential_mode = "user"
    await mcp_service.override_repo.session.commit()
    user_cred_id = await mcp_service.set_user_credential(
        server_id=server.id,
        user_id="u9",
        workspace_id="ws-test",
        plaintext="user-secret",
    )
    assert user_cred_id


async def test_workspace_override_with_null_credential_mode_inherits_server_scope(
    mcp_service: MCPServerService,
) -> None:
    """An enabled override row with credential_mode=NULL must inherit the
    server-level ``credential_scope`` rather than silently defaulting to 'org'.

    Regression for the bug where existing override rows backfilled to 'org'
    by the 09a4503eba8a migration broke user-scope OAuth installs.
    """
    server = await mcp_service.create(
        name="user-scope-server",
        server_url="https://user-scope",
        transport="streamable_http",
        auth_method="static",
        credential_scope="user",
    )

    await mcp_service.override_repo.upsert(
        workspace_id="ws-test",
        mcp_server_id=server.id,
        enabled=True,
        updated_by_user_id="u1",
    )
    override = await mcp_service.override_repo.get_for_workspace_and_server(
        workspace_id="ws-test", mcp_server_id=server.id
    )
    assert override is not None
    assert override.credential_mode is None, "new overrides must inherit, not lock to 'org'"

    # set_user_credential must be allowed because effective_mode falls back to
    # the server's 'user' scope.
    user_cred_id = await mcp_service.set_user_credential(
        server_id=server.id,
        user_id="u9",
        workspace_id="ws-test",
        plaintext="user-secret",
    )
    assert user_cred_id

    # And the org write path is still rejected (effective_mode != 'org').
    with pytest.raises(MCPCredentialPathMismatch):
        await mcp_service.set_workspace_credential(
            server_id=server.id,
            workspace_id="ws-test",
            plaintext="ws-secret",
        )


async def test_refresh_tools_for_oauth_server_uses_token_manager(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    cred_service: CredentialService,
    request_context: RequestContext,
) -> None:
    """``refresh_tools`` on an OAuth server must route through the token
    manager so an expired access token gets refreshed before discovery
    rather than blindly producing a 401.

    Regression for the case where Notion's stored access_token expired
    overnight, sync-tools kept feeding the dead token into discovery,
    and the UI showed ``Error / Not authenticated`` even though a usable
    refresh_token was sitting in the vault.
    """
    captured: dict[str, object] = {}

    async def _discover_success(
        _server: object, *, credential_or_token: str | None
    ) -> tuple[bool, list, None]:
        captured["token"] = credential_or_token
        return True, [], None

    monkeypatch.setattr("cubebox.services.mcp.discover_tools", _discover_success)
    monkeypatch.setattr(
        "cubebox.mcp.cubepi_admin_refresh.discover_tools_metadata", _discover_success
    )

    class StubTokenManager:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_valid_access_token(self, server: object, **_kwargs: object) -> str:
            self.calls.append(getattr(server, "id", "?"))
            return "fresh-access-token"

    stub = StubTokenManager()

    svc = MCPServerService(
        server_repo=MCPServerRepository(session, org_id=request_context.org_id),
        ws_cred_repo=WorkspaceMCPCredentialRepository(session, org_id=request_context.org_id),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=request_context.org_id),
        override_repo=WorkspaceMCPOverrideRepository(session, org_id=request_context.org_id),
        cred_service=cred_service,
        request_context=request_context,
        token_manager=stub,  # type: ignore[arg-type]
    )

    # Seed an OAuth+org server with a stale-looking access_token credential.
    cred_id = await cred_service.create(
        kind="mcp_oauth_access_token",
        name="mcp:notion:org:access",
        plaintext="stale-token",
    )
    from cubebox.mcp._constants import server_url_hash
    from cubebox.models import MCPServer

    server_repo = svc.server_repo
    server = MCPServer(
        org_id=request_context.org_id,
        name="catalog:notion",
        server_url="https://mcp.notion.com/mcp",
        server_url_hash=server_url_hash("https://mcp.notion.com/mcp"),
        transport="streamable_http",
        auth_method="oauth",
        credential_scope="org",
        credential_id=cred_id,
        created_by_user_id=request_context.user.id,
    )
    saved = await server_repo.add(server)

    await svc.refresh_tools(server_id=saved.id)

    assert stub.calls == [saved.id], "token manager must be consulted for OAuth servers"
    assert captured["token"] == "fresh-access-token", (
        "discovery must receive the refreshed token, not the stored stale one"
    )


async def test_refresh_tools_marks_unauthed_when_oauth_state_unusable(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    cred_service: CredentialService,
    request_context: RequestContext,
) -> None:
    """If the token manager raises ``OAuthInvalidServerState`` (e.g. expired
    access token + missing refresh_token metadata), the service must flip
    ``authed=False`` and write a descriptive ``last_error`` so the admin UI
    can show the Re-authenticate button. Letting the exception propagate
    would surface as a 500 and leave the server stuck in its old
    ``authed=true`` state.
    """
    from cubebox.mcp._constants import server_url_hash
    from cubebox.mcp.exceptions import OAuthInvalidServerState
    from cubebox.models import MCPServer

    async def _discover_should_not_run(
        _server: object, *, credential_or_token: str | None
    ) -> tuple[bool, list, None]:
        raise AssertionError("discovery must not run when refresh is impossible")

    monkeypatch.setattr("cubebox.services.mcp.discover_tools", _discover_should_not_run)
    monkeypatch.setattr(
        "cubebox.mcp.cubepi_admin_refresh.discover_tools_metadata",
        _discover_should_not_run,
    )

    class StubTokenManager:
        async def get_valid_access_token(self, server: object, **_kwargs: object) -> str:
            raise OAuthInvalidServerState(
                f"server {getattr(server, 'id', '?')} has no refresh_token_credential_id"
            )

    svc = MCPServerService(
        server_repo=MCPServerRepository(session, org_id=request_context.org_id),
        ws_cred_repo=WorkspaceMCPCredentialRepository(session, org_id=request_context.org_id),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=request_context.org_id),
        override_repo=WorkspaceMCPOverrideRepository(session, org_id=request_context.org_id),
        cred_service=cred_service,
        request_context=request_context,
        token_manager=StubTokenManager(),  # type: ignore[arg-type]
    )

    cred_id = await cred_service.create(
        kind="mcp_oauth_access_token",
        name="mcp:notion:org:access",
        plaintext="stale-token",
    )
    saved = await svc.server_repo.add(
        MCPServer(
            org_id=request_context.org_id,
            name="catalog:notion",
            server_url="https://mcp.notion.com/mcp",
            server_url_hash=server_url_hash("https://mcp.notion.com/mcp"),
            transport="streamable_http",
            auth_method="oauth",
            credential_scope="org",
            credential_id=cred_id,
            authed=True,  # was previously usable; now refresh impossible
            created_by_user_id=request_context.user.id,
        )
    )

    # Must NOT raise — service translates the exception into a soft failure.
    await svc.refresh_tools(server_id=saved.id)

    refreshed = await svc.server_repo.get(saved.id)
    assert refreshed is not None
    assert refreshed.authed is False, "must flip authed=False so UI shows Re-authenticate"
    assert refreshed.last_error is not None
    assert "OAuth re-authentication required" in refreshed.last_error


# ---------------------------------------------------------------------------
# Four-layer install service invariants (cubebox.services.mcp_installs)
#
# These tests cover the **pure** invariants of the install service: the
# auth_method short-circuit on defaults, and the scope-vs-fk validation on
# create_static_grant. They use mocked repos / credential service so they
# stay independent of SQLModel / async session fixtures — the contract is
# what the service computes BEFORE any I/O.
# ---------------------------------------------------------------------------


def test_auth_method_none_resolves_not_required_defaults() -> None:
    from cubebox.services.mcp_installs import install_defaults_for_auth_method

    defaults = install_defaults_for_auth_method("none", "user")

    assert defaults.auth_status == "not_required"
    assert defaults.credential_policy == "none"


def test_static_auth_uses_requested_policy() -> None:
    from cubebox.services.mcp_installs import install_defaults_for_auth_method

    defaults = install_defaults_for_auth_method("static", "workspace")

    assert defaults.auth_status == "pending"
    assert defaults.credential_policy == "workspace"


class _StubCredService:
    """Records vault create calls so the test can assert it was NOT invoked.

    The scope-vs-fk validation MUST run before any vault write — a vault
    write on a wrongly shaped grant would orphan an encrypted secret in
    the credentials table with no grant row pointing at it.
    """

    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []

    async def create(self, *, kind: str, name: str, plaintext: str) -> str:
        self.create_calls.append({"kind": kind, "name": name, "plaintext": plaintext})
        return "cred-stub"


class _StubGrantRepo:
    def __init__(self) -> None:
        self.added: list[Any] = []

    async def add(self, grant: Any) -> Any:
        self.added.append(grant)
        return grant


def _make_install_service_with_stubs() -> tuple[Any, _StubCredService, _StubGrantRepo]:
    from cubebox.services.mcp_installs import MCPConnectorInstallService

    cred_stub = _StubCredService()
    grant_stub = _StubGrantRepo()
    svc = MCPConnectorInstallService(
        install_repo=cast(Any, object()),
        state_repo=cast(Any, object()),
        grant_repo=cast(Any, grant_stub),
        cred_service=cast(Any, cred_stub),
        org_id="org-test",
        actor_user_id="u1",
    )
    return svc, cred_stub, grant_stub


async def test_create_static_grant_org_scope_rejects_workspace_id() -> None:
    """grant_scope='org' with workspace_id set must raise BEFORE vault write."""
    svc, cred_stub, grant_stub = _make_install_service_with_stubs()

    with pytest.raises(ValueError, match="grant_scope='org'"):
        await svc.create_static_grant(
            install_id="mcins-x",
            grant_scope="org",
            plaintext="secret",
            workspace_id="ws-1",
            user_id=None,
        )

    assert cred_stub.create_calls == [], "vault must not be touched on shape mismatch"
    assert grant_stub.added == [], "grant must not be persisted on shape mismatch"


async def test_create_static_grant_workspace_scope_requires_workspace_id() -> None:
    """grant_scope='workspace' without workspace_id must raise BEFORE vault write."""
    svc, cred_stub, grant_stub = _make_install_service_with_stubs()

    with pytest.raises(ValueError, match="grant_scope='workspace'"):
        await svc.create_static_grant(
            install_id="mcins-x",
            grant_scope="workspace",
            plaintext="secret",
            workspace_id=None,
            user_id=None,
        )

    assert cred_stub.create_calls == []
    assert grant_stub.added == []


async def test_create_static_grant_user_scope_requires_user_id() -> None:
    """grant_scope='user' must carry BOTH workspace_id and user_id.

    User grants are scoped per-workspace by the DB check constraint;
    a user-scope request without user_id (or without workspace_id)
    must reject before any vault write so a misrouted call from
    ``/grants/me`` can't silently degrade into a workspace-scope grant.
    """
    svc, cred_stub, grant_stub = _make_install_service_with_stubs()

    with pytest.raises(ValueError, match="grant_scope='user'"):
        await svc.create_static_grant(
            install_id="mcins-x",
            grant_scope="user",
            plaintext="secret",
            workspace_id="ws-1",
            user_id=None,
        )

    assert cred_stub.create_calls == []
    assert grant_stub.added == []
