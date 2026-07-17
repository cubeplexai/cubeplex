"""Connector-centric MCP runtime invariants."""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.mcp._constants import server_url_hash
from cubeplex.mcp.effective import MCPEffectiveConnectorService
from cubeplex.models import (
    Credential,
    MCPConnector,
    MCPConnectorTemplate,
    MCPCredentialGrant,
    Organization,
    User,
    Workspace,
)
from cubeplex.repositories.mcp import (
    MCPConnectorRepository,
    MCPConnectorTemplateRepository,
    MCPCredentialGrantRepository,
    MCPTemplateSettingsRepository,
    MCPWorkspaceConnectorStateRepository,
)

pytestmark = pytest.mark.e2e


async def _create_connector_fixture(
    db_session: AsyncSession,
) -> tuple[str, str, str, str, str]:
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

    # Template required (template_id FK is NOT NULL).
    template = MCPConnectorTemplate(
        slug=f"cleanup-{suffix}",
        name=f"Cleanup Template {suffix}",
        description="test",
        provider="test",
        server_url=f"https://cleanup-{suffix}.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_policy="workspace",
        scope="global",
    )
    db_session.add(template)
    await db_session.flush()

    connector = await MCPConnectorRepository(db_session, org_id=org.id).add(
        MCPConnector(
            org_id=org.id,
            template_id=template.id,
            name=f"Cleanup Connector {suffix}",
            server_url=f"https://cleanup-{suffix}.example.com/mcp",
            server_url_hash=server_url_hash(f"https://cleanup-{suffix}.example.com/mcp"),
            transport="streamable_http",
            default_credential_policy="workspace",
            status="active",
            created_by_user_id=user.id,
        )
    )
    return org.id, workspace.id, user.id, connector.id, suffix


async def test_workspace_state_is_keyed_by_connector_id(
    db_session: AsyncSession,
) -> None:
    org_id, workspace_id, user_id, connector_id, _suffix = await _create_connector_fixture(
        db_session
    )
    repo = MCPWorkspaceConnectorStateRepository(db_session, org_id=org_id)

    saved = await repo.upsert_for_connector(
        workspace_id=workspace_id,
        connector_id=connector_id,
        enabled=True,
        credential_policy="workspace",
        enablement_source="workspace_manual",
        updated_by_user_id=user_id,
    )
    found = await repo.get_by_connector(workspace_id, connector_id)

    assert found is not None
    assert found.id == saved.id
    assert found.connector_id == connector_id
    assert found.credential_policy == "workspace"

    updated = await repo.upsert_for_connector(
        workspace_id=workspace_id,
        connector_id=connector_id,
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
    org_id, workspace_id, user_id, connector_id, suffix = await _create_connector_fixture(
        db_session
    )
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
            connector_id=connector_id,
            grant_scope="org",
            auth_method="static",
            credential_id=credentials[0].id,
            created_by_user_id=user_id,
        )
    )
    await repo.add(
        MCPCredentialGrant(
            org_id=org_id,
            connector_id=connector_id,
            grant_scope="workspace",
            auth_method="static",
            workspace_id=workspace_id,
            credential_id=credentials[1].id,
            created_by_user_id=user_id,
        )
    )
    await repo.add(
        MCPCredentialGrant(
            org_id=org_id,
            connector_id=connector_id,
            grant_scope="user",
            auth_method="static",
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
    assert org_grant.connector_id == connector_id
    assert org_grant.credential_id == credentials[0].id
    assert workspace_grant is not None
    assert workspace_grant.connector_id == connector_id
    assert workspace_grant.credential_id == credentials[1].id
    assert user_grant is not None
    assert user_grant.connector_id == connector_id
    assert user_grant.credential_id == credentials[2].id


async def test_effective_runtime_resolves_workspace_grant_by_connector_id(
    db_session: AsyncSession,
) -> None:
    org_id, workspace_id, user_id, connector_id, suffix = await _create_connector_fixture(
        db_session
    )
    credential = Credential(
        org_id=org_id,
        kind="mcp_server",
        name=f"cleanup-effective-workspace-{suffix}",
        value_encrypted=b"workspace-secret",
    )
    db_session.add(credential)
    await db_session.flush()

    await MCPWorkspaceConnectorStateRepository(db_session, org_id=org_id).upsert_for_connector(
        workspace_id=workspace_id,
        connector_id=connector_id,
        enabled=True,
        credential_policy="workspace",
        enablement_source="workspace_manual",
        updated_by_user_id=user_id,
    )
    await MCPCredentialGrantRepository(db_session, org_id=org_id).add(
        MCPCredentialGrant(
            org_id=org_id,
            connector_id=connector_id,
            grant_scope="workspace",
            auth_method="static",
            workspace_id=workspace_id,
            credential_id=credential.id,
            created_by_user_id=user_id,
        )
    )

    service = MCPEffectiveConnectorService(
        template_repo=MCPConnectorTemplateRepository(db_session),
        settings_repo=MCPTemplateSettingsRepository(db_session, org_id=org_id),
        connector_repo=MCPConnectorRepository(db_session, org_id=org_id),
        state_repo=MCPWorkspaceConnectorStateRepository(db_session, org_id=org_id),
        grant_repo=MCPCredentialGrantRepository(db_session, org_id=org_id),
        org_id=org_id,
    )

    rows = await service.list_for_workspace_user(workspace_id, user_id)

    assert len(rows) == 1
    assert rows[0].connector.id == connector_id
    assert rows[0].grant is not None
    assert rows[0].grant.connector_id == connector_id
    assert rows[0].usable is True
