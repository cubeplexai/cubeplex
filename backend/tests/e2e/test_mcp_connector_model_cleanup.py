"""Connector-centric MCP repository invariants."""

from __future__ import annotations

import secrets

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.mcp._constants import server_url_hash
from cubebox.mcp.effective import MCPEffectiveConnectorService
from cubebox.models import (
    Credential,
    MCPConnector,
    MCPConnectorInstall,
    MCPCredentialGrant,
    MCPWorkspaceConnectorState,
    Organization,
    User,
    Workspace,
)
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
    MCPConnectorRepository,
    MCPConnectorTemplateRepository,
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


async def test_effective_runtime_resolves_workspace_grant_by_connector_id(
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
    install_repo = MCPConnectorInstallRepository(db_session, org_id=org_id)
    legacy_install = await install_repo.add(
        MCPConnectorInstall(
            org_id=org_id,
            workspace_id=None,
            install_scope="org",
            template_id=None,
            name=f"Legacy Grant Owner {suffix}",
            server_url=f"https://legacy-grant-owner-{suffix}.example.com/mcp",
            server_url_hash=server_url_hash(f"https://legacy-grant-owner-{suffix}.example.com/mcp"),
            transport="streamable_http",
            auth_method="static",
            default_credential_policy="workspace",
            install_state="uninstalled",
            created_by_user_id=user_id,
        )
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
        install_id=install_id,
        enabled=True,
        credential_policy="workspace",
        enablement_source="workspace_manual",
        updated_by_user_id=user_id,
    )
    await MCPCredentialGrantRepository(db_session, org_id=org_id).add(
        MCPCredentialGrant(
            org_id=org_id,
            install_id=legacy_install.id,
            connector_id=connector_id,
            grant_scope="workspace",
            workspace_id=workspace_id,
            credential_id=credential.id,
            created_by_user_id=user_id,
        )
    )

    service = MCPEffectiveConnectorService(
        template_repo=MCPConnectorTemplateRepository(db_session),
        install_repo=install_repo,
        state_repo=MCPWorkspaceConnectorStateRepository(db_session, org_id=org_id),
        grant_repo=MCPCredentialGrantRepository(db_session, org_id=org_id),
        org_id=org_id,
    )

    rows = await service.list_for_workspace_user(workspace_id, user_id)

    assert len(rows) == 1
    assert rows[0].install.id == install_id
    assert rows[0].grant is not None
    assert rows[0].grant.connector_id == connector_id
    assert rows[0].grant.install_id == legacy_install.id
    assert rows[0].usable is True


async def test_static_grant_replace_uses_connector_scope_key(
    db_session: AsyncSession,
) -> None:
    from cubebox.credentials.encryption import FernetBackend
    from cubebox.repositories.credential import CredentialRepository
    from cubebox.services.credential import CredentialService
    from cubebox.services.mcp_installs import MCPConnectorInstallService

    (
        org_id,
        workspace_id,
        user_id,
        connector_id,
        install_id,
        suffix,
    ) = await _create_connector_fixture(db_session)
    install_repo = MCPConnectorInstallRepository(db_session, org_id=org_id)
    old_owner = await install_repo.add(
        MCPConnectorInstall(
            org_id=org_id,
            workspace_id=None,
            install_scope="org",
            template_id=None,
            name=f"Old Static Grant Owner {suffix}",
            server_url=f"https://old-static-grant-owner-{suffix}.example.com/mcp",
            server_url_hash=server_url_hash(
                f"https://old-static-grant-owner-{suffix}.example.com/mcp"
            ),
            transport="streamable_http",
            auth_method="static",
            default_credential_policy="workspace",
            install_state="uninstalled",
            created_by_user_id=user_id,
        )
    )
    old_credential = Credential(
        org_id=org_id,
        kind="mcp_server",
        name=f"cleanup-static-old-{suffix}",
        value_encrypted=b"old-secret",
    )
    db_session.add(old_credential)
    await db_session.flush()

    grant_repo = MCPCredentialGrantRepository(db_session, org_id=org_id)
    existing = await grant_repo.add(
        MCPCredentialGrant(
            org_id=org_id,
            install_id=old_owner.id,
            connector_id=connector_id,
            grant_scope="workspace",
            workspace_id=workspace_id,
            credential_id=old_credential.id,
            created_by_user_id=user_id,
        )
    )
    service = MCPConnectorInstallService(
        install_repo=install_repo,
        state_repo=MCPWorkspaceConnectorStateRepository(db_session, org_id=org_id),
        grant_repo=grant_repo,
        cred_service=CredentialService(
            CredentialRepository(db_session, org_id=org_id),
            FernetBackend([Fernet.generate_key()]),
            org_id=org_id,
            actor_user_id=user_id,
        ),
        org_id=org_id,
        actor_user_id=user_id,
        connector_repo=MCPConnectorRepository(db_session, org_id=org_id),
    )

    updated = await service.create_static_grant(
        install_id=install_id,
        grant_scope="workspace",
        workspace_id=workspace_id,
        plaintext="new-secret",
        name=f"cleanup-static-new-{suffix}",
    )

    assert updated.id == existing.id
    assert updated.install_id == install_id
    assert updated.connector_id == connector_id
    assert updated.credential_id != old_credential.id


async def test_workspace_enable_uses_connector_state_without_workspace_install(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    from tests.e2e.conftest import _seed_four_layer_template

    suffix = secrets.token_hex(4)
    template_id = await _seed_four_layer_template(
        slug=f"cleanup-org-first-{suffix}",
        name=f"Cleanup Org First {suffix}",
        supported_auth_methods=["none"],
        default_credential_policy="none",
    )
    client, workspace_id = admin_client

    org_add = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": template_id,
            "install_scope": "org",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert org_add.status_code == 201, org_add.text
    connector_id = org_add.json()["connector_id"]

    ws_enable = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert ws_enable.status_code == 201, ws_enable.text
    assert ws_enable.json()["connector_id"] == connector_id

    workspace_installs = (
        (
            await db_session.execute(
                select(MCPConnectorInstall).where(
                    MCPConnectorInstall.template_id == template_id,
                    MCPConnectorInstall.workspace_id == workspace_id,
                    MCPConnectorInstall.install_state == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    states = (
        (
            await db_session.execute(
                select(MCPWorkspaceConnectorState).where(
                    MCPWorkspaceConnectorState.workspace_id == workspace_id,
                    MCPWorkspaceConnectorState.connector_id == connector_id,
                )
            )
        )
        .scalars()
        .all()
    )

    assert list(workspace_installs) == []
    assert len(list(states)) == 1


async def test_workspace_first_enable_does_not_create_workspace_install(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    from tests.e2e.conftest import _seed_four_layer_template

    suffix = secrets.token_hex(4)
    template_id = await _seed_four_layer_template(
        slug=f"cleanup-ws-enable-{suffix}",
        name=f"Cleanup WS Enable {suffix}",
        supported_auth_methods=["none"],
        default_credential_policy="none",
    )
    client, workspace_id = admin_client

    ws_enable = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert ws_enable.status_code == 201, ws_enable.text
    connector_id = ws_enable.json()["connector_id"]

    workspace_installs = (
        (
            await db_session.execute(
                select(MCPConnectorInstall).where(
                    MCPConnectorInstall.template_id == template_id,
                    MCPConnectorInstall.workspace_id == workspace_id,
                    MCPConnectorInstall.install_state == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    org_installs = (
        (
            await db_session.execute(
                select(MCPConnectorInstall).where(
                    MCPConnectorInstall.template_id == template_id,
                    MCPConnectorInstall.workspace_id.is_(None),
                    MCPConnectorInstall.install_state == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    state = (
        await db_session.execute(
            select(MCPWorkspaceConnectorState).where(
                MCPWorkspaceConnectorState.workspace_id == workspace_id,
                MCPWorkspaceConnectorState.connector_id == connector_id,
            )
        )
    ).scalar_one()

    assert list(workspace_installs) == []
    assert len(list(org_installs)) == 1
    assert state.install_id == org_installs[0].id


async def test_org_add_promotes_workspace_install_without_leaving_workspace_install(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    from tests.e2e.conftest import _seed_four_layer_template

    suffix = secrets.token_hex(4)
    template_id = await _seed_four_layer_template(
        slug=f"cleanup-ws-first-{suffix}",
        name=f"Cleanup WS First {suffix}",
        supported_auth_methods=["none"],
        default_credential_policy="none",
    )
    client, workspace_id = admin_client

    ws_enable = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert ws_enable.status_code == 201, ws_enable.text
    connector_id = ws_enable.json()["connector_id"]

    org_add = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": template_id,
            "install_scope": "org",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert org_add.status_code == 201, org_add.text
    assert org_add.json()["connector_id"] == connector_id

    workspace_installs = (
        (
            await db_session.execute(
                select(MCPConnectorInstall).where(
                    MCPConnectorInstall.template_id == template_id,
                    MCPConnectorInstall.workspace_id == workspace_id,
                    MCPConnectorInstall.install_state == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    org_installs = (
        (
            await db_session.execute(
                select(MCPConnectorInstall).where(
                    MCPConnectorInstall.template_id == template_id,
                    MCPConnectorInstall.workspace_id.is_(None),
                    MCPConnectorInstall.install_state == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    state = (
        await db_session.execute(
            select(MCPWorkspaceConnectorState).where(
                MCPWorkspaceConnectorState.workspace_id == workspace_id,
                MCPWorkspaceConnectorState.connector_id == connector_id,
            )
        )
    ).scalar_one()

    assert list(workspace_installs) == []
    assert len(list(org_installs)) == 1
    assert state.install_id == org_installs[0].id
