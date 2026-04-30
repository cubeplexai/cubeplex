"""Tests for MCP connector repositories."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.models import (
    MCPServer,
    UserMCPCredential,
    WorkspaceMCPBinding,
    WorkspaceMCPCredential,
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
    from cubebox.repositories.mcp import MCPServerRepository, WorkspaceMCPBindingRepository

    servers = MCPServerRepository(session, org_id="org-1")
    bindings = WorkspaceMCPBindingRepository(session, org_id="org-1")
    owned = await servers.add(
        _server(name="owned", server_url_hash="owned", owner_workspace_id="ws-1")
    )
    org_bound = await servers.add(_server(name="bound", server_url_hash="bound"))
    await servers.add(_server(name="disabled", server_url_hash="disabled"))
    await servers.add(_server(name="not-authed", server_url_hash="not-authed", authed=False))
    await bindings.add(
        WorkspaceMCPBinding(
            org_id="wrong",
            workspace_id="ws-1",
            mcp_server_id=org_bound.id,
            enabled=True,
            created_by_user_id="user-1",
        )
    )

    visible = await servers.list_for_workspace("ws-1")

    assert {server.id for server in visible} == {owned.id, org_bound.id}


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


async def test_binding_repository_upsert_bulk_replaces_bindings(
    session: AsyncSession,
) -> None:
    from cubebox.repositories.mcp import WorkspaceMCPBindingRepository

    repo = WorkspaceMCPBindingRepository(session, org_id="org-1")
    await repo.upsert_bulk(
        mcp_server_id="mcp-1",
        bindings=[("ws-1", True), ("ws-2", False)],
        created_by_user_id="user-1",
    )
    await repo.upsert_bulk(
        mcp_server_id="mcp-1",
        bindings=[("ws-2", True), ("ws-3", True)],
        created_by_user_id="user-1",
    )

    rows = await repo.list_for_server("mcp-1")

    assert {(row.workspace_id, row.enabled) for row in rows} == {
        ("ws-2", True),
        ("ws-3", True),
    }
