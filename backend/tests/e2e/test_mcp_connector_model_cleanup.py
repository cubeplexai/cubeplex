"""Connector-centric MCP repository invariants."""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.mcp._constants import server_url_hash
from cubebox.models import (
    Credential,
    MCPConnector,
    MCPConnectorInstall,
    MCPCredentialGrant,
    Organization,
    User,
    Workspace,
)
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
    MCPConnectorRepository,
    MCPCredentialGrantRepository,
    MCPWorkspaceConnectorStateRepository,
)

pytestmark = pytest.mark.e2e


async def _create_connector_fixture(
    db_session: AsyncSession,
) -> tuple[str, str, str, str, str, str]:
    suffix = secrets.token_hex(4)
    org = Organization(
        name=f"MCP Cleanup Org {suffix}",
        slug=f"mcp-cleanup-org-{suffix}",
    )
    db_session.add(org)
    await db_session.flush()

    workspace = Workspace(org_id=org.id, name=f"MCP Cleanup Workspace {suffix}")
    user = User(
        email=f"mcp-cleanup-{suffix}@example.com",
        hashed_password="not-used",
        is_active=True,
        is_verified=True,
    )
    db_session.add(workspace)
    db_session.add(user)
    await db_session.flush()

    connector = await MCPConnectorRepository(db_session, org_id=org.id).add(
        MCPConnector(
            org_id=org.id,
            template_id=None,
            name=f"Cleanup Connector {suffix}",
            server_url=f"https://cleanup-{suffix}.example.com/mcp",
            server_url_hash=server_url_hash(f"https://cleanup-{suffix}.example.com/mcp"),
            transport="streamable_http",
            auth_method="static",
            status="active",
            created_by_user_id=user.id,
        )
    )

    # Legacy compatibility only: the current schema still requires install_id.
    install = await MCPConnectorInstallRepository(db_session, org_id=org.id).add(
        MCPConnectorInstall(
            org_id=org.id,
            workspace_id=None,
            install_scope="org",
            template_id=None,
            name=f"Cleanup Connector {suffix}",
            server_url=f"https://cleanup-{suffix}.example.com/mcp",
            server_url_hash=server_url_hash(f"https://cleanup-{suffix}.example.com/mcp"),
            transport="streamable_http",
            auth_method="static",
            default_credential_policy="workspace",
            created_by_user_id=user.id,
        )
    )

    return org.id, workspace.id, user.id, connector.id, install.id, suffix


async def test_workspace_state_is_keyed_by_connector_id(
    db_session: AsyncSession,
) -> None:
    (
        org_id,
        workspace_id,
        user_id,
        connector_id,
        install_id,
        _suffix,
    ) = await _create_connector_fixture(db_session)
    repo = MCPWorkspaceConnectorStateRepository(db_session, org_id=org_id)

    saved = await repo.upsert_for_connector(
        workspace_id=workspace_id,
        connector_id=connector_id,
        install_id=install_id,
        enabled=True,
        credential_policy="workspace",
        enablement_source="workspace_manual",
        updated_by_user_id=user_id,
    )
    found = await repo.get_by_connector(workspace_id, connector_id)

    assert found is not None
    assert found.id == saved.id
    assert found.install_id == install_id
    assert found.connector_id == connector_id
    assert found.credential_policy == "workspace"

    updated = await repo.upsert_for_connector(
        workspace_id=workspace_id,
        connector_id=connector_id,
        install_id=install_id,
        enabled=False,
        credential_policy="org",
        enablement_source="admin_manual",
        updated_by_user_id=user_id,
    )

    assert updated.id == saved.id
    assert updated.enabled is False
    assert updated.credential_policy == "org"
    assert updated.enablement_source == "admin_manual"


async def test_credential_grants_are_keyed_by_connector_id(
    db_session: AsyncSession,
) -> None:
    (
        org_id,
        workspace_id,
        user_id,
        connector_id,
        install_id,
        suffix,
    ) = await _create_connector_fixture(db_session)
    credentials = [
        Credential(
            org_id=org_id,
            kind="mcp_server",
            name=f"cleanup-{scope}-{suffix}",
            value_encrypted=f"{scope}-secret".encode(),
        )
        for scope in ("org", "workspace", "user")
    ]
    db_session.add_all(credentials)
    await db_session.flush()

    repo = MCPCredentialGrantRepository(db_session, org_id=org_id)
    await repo.add(
        MCPCredentialGrant(
            org_id=org_id,
            install_id=install_id,
            connector_id=connector_id,
            grant_scope="org",
            credential_id=credentials[0].id,
            created_by_user_id=user_id,
        )
    )
    await repo.add(
        MCPCredentialGrant(
            org_id=org_id,
            install_id=install_id,
            connector_id=connector_id,
            grant_scope="workspace",
            workspace_id=workspace_id,
            credential_id=credentials[1].id,
            created_by_user_id=user_id,
        )
    )
    await repo.add(
        MCPCredentialGrant(
            org_id=org_id,
            install_id=install_id,
            connector_id=connector_id,
            grant_scope="user",
            workspace_id=workspace_id,
            user_id=user_id,
            credential_id=credentials[2].id,
            created_by_user_id=user_id,
        )
    )

    org_grant = await repo.get_org_grant_for_connector(connector_id)
    workspace_grant = await repo.get_workspace_grant_for_connector(connector_id, workspace_id)
    user_grant = await repo.get_user_grant_for_connector(
        connector_id,
        user_id,
        workspace_id=workspace_id,
    )

    assert org_grant is not None
    assert org_grant.install_id == install_id
    assert org_grant.connector_id == connector_id
    assert org_grant.credential_id == credentials[0].id
    assert workspace_grant is not None
    assert workspace_grant.install_id == install_id
    assert workspace_grant.connector_id == connector_id
    assert workspace_grant.credential_id == credentials[1].id
    assert user_grant is not None
    assert user_grant.install_id == install_id
    assert user_grant.connector_id == connector_id
    assert user_grant.credential_id == credentials[2].id
