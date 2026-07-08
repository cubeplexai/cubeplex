"""MCP credential layering API invariants."""

from __future__ import annotations

import secrets

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.usefixtures("stub_discover_tools")


async def test_org_add_does_not_409_when_workspace_install_exists(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    """Workspace credentials and org provisioning are separate layers."""
    from tests.e2e.conftest import _seed_four_layer_template

    suffix = secrets.token_hex(4)
    template_id = await _seed_four_layer_template(
        slug=f"layering-noauth-{suffix}",
        name=f"Layering No Auth {suffix}",
        supported_auth_methods=["none"],
        default_credential_policy="none",
    )
    client, workspace_id = admin_client

    ws_install = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert ws_install.status_code == 201, ws_install.text

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
    assert connector_id.startswith("mcpco-")

    from cubebox.models import MCPWorkspaceConnectorState

    state = (
        await db_session.execute(
            select(MCPWorkspaceConnectorState).where(
                MCPWorkspaceConnectorState.connector_id == connector_id
            )
        )
    ).scalar_one()
    assert state.workspace_id == workspace_id
    assert state.credential_policy == "none"


async def test_workspace_enable_does_not_409_when_org_connector_exists(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
) -> None:
    """Workspace enablement is state over the org connector identity."""
    from tests.e2e.conftest import _seed_four_layer_template

    suffix = secrets.token_hex(4)
    template_id = await _seed_four_layer_template(
        slug=f"layering-org-first-{suffix}",
        name=f"Layering Org First {suffix}",
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

    from cubebox.models import MCPWorkspaceConnectorState

    state = (
        await db_session.execute(
            select(MCPWorkspaceConnectorState).where(
                MCPWorkspaceConnectorState.connector_id == connector_id,
                MCPWorkspaceConnectorState.workspace_id == workspace_id,
            )
        )
    ).scalar_one()
    assert state.credential_policy == "none"
