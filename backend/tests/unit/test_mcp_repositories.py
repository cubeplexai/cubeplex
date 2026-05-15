"""Tests for MCP connector repositories."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.models import (
    MCPServer,
    UserMCPCredential,
    WorkspaceMCPCredential,
    WorkspaceMCPOverride,
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _server(**overrides: object) -> MCPServer:
    values = {
        "org_id": "org-evil",
        "name": "github",
        "server_url": "https://example.com/mcp",
        "server_url_hash": "hash",
        "transport": "streamable_http",
        "auth_method": "static",
        "credential_scope": "org",
        "credential_id": "cred-1",
        "authed": True,
        "created_by_user_id": "user-1",
    }
    values.update(overrides)
    return MCPServer(**values)


async def test_mcp_server_repository_enforces_org_scope(session: AsyncSession) -> None:
    from cubebox.repositories.mcp import MCPServerRepository

    repo = MCPServerRepository(session, org_id="org-1")

    saved = await repo.add(_server())

    assert saved.org_id == "org-1"
    assert await repo.get(saved.id) == saved
    assert await MCPServerRepository(session, org_id="org-2").get(saved.id) is None
    assert await repo.find_by_url_hash(owner_workspace_id=None, server_url_hash="hash") == saved
    assert await repo.find_by_credential_id("cred-1") == [saved]


async def test_mcp_server_repository_lists_visible_for_workspace(
    session: AsyncSession,
) -> None:
    """Org installs are invisible by default; enabled=True overrides opt in."""
    from cubebox.repositories.mcp import MCPServerRepository, WorkspaceMCPOverrideRepository

    servers = MCPServerRepository(session, org_id="org-1")
    overrides = WorkspaceMCPOverrideRepository(session, org_id="org-1")

    owned = await servers.add(
        _server(name="owned", server_url_hash="owned", owner_workspace_id="ws-1")
    )
    inherited = await servers.add(_server(name="inherited", server_url_hash="inherited"))
    not_enabled = await servers.add(_server(name="not-enabled", server_url_hash="not-enabled"))
    await servers.add(_server(name="not-authed", server_url_hash="not-authed", authed=False))

    # Workspace ws-1 explicitly enables the first org-wide install.
    await overrides.upsert(
        workspace_id="ws-1",
        mcp_server_id=inherited.id,
        enabled=True,
        updated_by_user_id="user-1",
    )
    # not_enabled has no override row — invisible by default.
    _ = not_enabled

    visible = await servers.list_for_workspace("ws-1")
    assert {server.id for server in visible} == {owned.id, inherited.id}


async def test_workspace_and_user_credential_repositories_scope_by_org(
    session: AsyncSession,
) -> None:
    from cubebox.repositories.mcp import (
        UserMCPCredentialRepository,
        WorkspaceMCPCredentialRepository,
    )

    ws_repo = WorkspaceMCPCredentialRepository(session, org_id="org-1")
    user_repo = UserMCPCredentialRepository(session, org_id="org-1")

    ws_row = await ws_repo.add(
        WorkspaceMCPCredential(
            org_id="wrong",
            workspace_id="ws-1",
            mcp_server_id="mcp-1",
            credential_id="cred-ws",
            created_by_user_id="user-1",
        )
    )
    user_row = await user_repo.add(
        UserMCPCredential(
            org_id="wrong",
            user_id="user-1",
            mcp_server_id="mcp-1",
            credential_id="cred-user",
        )
    )

    assert ws_row.org_id == "org-1"
    assert user_row.org_id == "org-1"
    assert await ws_repo.find_by_credential_id("cred-ws") == [ws_row]
    assert await user_repo.find_by_credential_id("cred-user") == [user_row]


async def test_workspace_mcp_override_repository_upsert_idempotent(
    session: AsyncSession,
) -> None:
    from cubebox.repositories.mcp import WorkspaceMCPOverrideRepository

    repo = WorkspaceMCPOverrideRepository(session, org_id="org-1")

    first = await repo.upsert(
        workspace_id="ws-1",
        mcp_server_id="mcp-1",
        enabled=False,
        updated_by_user_id="user-1",
    )
    assert first.enabled is False

    second = await repo.upsert(
        workspace_id="ws-1",
        mcp_server_id="mcp-1",
        enabled=False,
        updated_by_user_id="user-2",
    )
    assert second.id == first.id
    assert second.updated_by_user_id == "user-2"

    rows = await repo.list_for_workspace("ws-1")
    assert len(rows) == 1


async def test_workspace_mcp_override_delete_clears_row(session: AsyncSession) -> None:
    from cubebox.repositories.mcp import WorkspaceMCPOverrideRepository

    repo = WorkspaceMCPOverrideRepository(session, org_id="org-1")
    await repo.upsert(
        workspace_id="ws-1",
        mcp_server_id="mcp-1",
        enabled=False,
        updated_by_user_id="user-1",
    )

    await repo.delete(workspace_id="ws-1", mcp_server_id="mcp-1")
    assert (
        await repo.get_for_workspace_and_server(workspace_id="ws-1", mcp_server_id="mcp-1") is None
    )


async def test_workspace_mcp_override_scoped_by_org(session: AsyncSession) -> None:
    from cubebox.repositories.mcp import WorkspaceMCPOverrideRepository

    a = WorkspaceMCPOverrideRepository(session, org_id="org-A")
    b = WorkspaceMCPOverrideRepository(session, org_id="org-B")

    saved = await a.upsert(
        workspace_id="ws-1",
        mcp_server_id="mcp-1",
        enabled=False,
        updated_by_user_id="user-1",
    )
    assert saved.org_id == "org-A"
    # Other org cannot see the override row.
    assert await b.get_for_workspace_and_server(workspace_id="ws-1", mcp_server_id="mcp-1") is None


# Sanity: keep ``WorkspaceMCPOverride`` importable from the model module.
def test_override_model_importable() -> None:
    assert WorkspaceMCPOverride.__tablename__ == "workspace_mcp_overrides"


# ---------------------------------------------------------------------------
# Four-layer repository contracts.
# ---------------------------------------------------------------------------


async def test_connector_template_repository_upserts_by_slug(session: AsyncSession) -> None:
    from cubebox.repositories.mcp import MCPConnectorTemplateRepository

    repo = MCPConnectorTemplateRepository(session)
    row = await repo.upsert_by_slug(
        slug="github",
        name="GitHub",
        description="GitHub MCP server.",
        provider="GitHub",
        server_url="https://github.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_policy="user",
    )

    assert row.slug == "github"
    assert row.default_credential_policy == "user"

    # Idempotent: re-upserting the same slug returns the existing row id.
    row2 = await repo.upsert_by_slug(
        slug="github",
        name="GitHub (renamed)",
        description="updated",
        provider="GitHub",
        server_url="https://github.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_policy="user",
    )
    assert row2.id == row.id
    assert row2.name == "GitHub (renamed)"


async def test_credential_grant_repository_scopes_user_grants(
    session: AsyncSession,
) -> None:
    from cubebox.models import MCPCredentialGrant
    from cubebox.repositories.mcp import MCPCredentialGrantRepository

    repo = MCPCredentialGrantRepository(session, org_id="org-1")
    await repo.add(
        MCPCredentialGrant(
            org_id="org-1",
            install_id="mcins-1",
            grant_scope="user",
            workspace_id="ws-1",
            user_id="user-1",
            credential_id="cred-1",
            created_by_user_id="user-1",
        )
    )

    assert (
        await repo.get_user_grant(install_id="mcins-1", user_id="user-1", workspace_id="ws-1")
        is not None
    )
    # No workspace filter still finds the grant by (install, user).
    assert await repo.get_user_grant(install_id="mcins-1", user_id="user-1") is not None
    # Different user → no grant.
    assert await repo.get_user_grant(install_id="mcins-1", user_id="user-2") is None
    # Different workspace → no grant when the filter is applied.
    assert (
        await repo.get_user_grant(install_id="mcins-1", user_id="user-1", workspace_id="ws-other")
        is None
    )

    # Cross-org repo can't see this grant.
    other = MCPCredentialGrantRepository(session, org_id="org-2")
    assert await other.get_user_grant(install_id="mcins-1", user_id="user-1") is None
