"""E2E tests for the workspace override route — Phase 3.6.

``PATCH /api/v1/ws/{ws}/mcp/org-installs/{install_id}/override``

Disable removes an org-wide install from the workspace's view; re-enable
deletes the override row (default-on inheritance).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository


@pytest_asyncio.fixture
async def catalog_id(db_session: AsyncSession) -> AsyncIterator[str]:
    repo = MCPCatalogConnectorRepository(db_session)
    row = await repo.upsert_by_slug(
        slug="github",
        name="GitHub",
        description="GitHub MCP server.",
        provider="GitHub",
        server_url="https://github.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_scope="org",
        static_form_fields=[{"name": "token", "label": "API token", "secret": True}],
        static_auth_header_template="Bearer {token}",
    )
    await db_session.commit()
    yield row.id


# Apply the shared MCP discover-tools stub to every test in this module.
pytestmark = pytest.mark.usefixtures("stub_discover_tools")


async def test_workspace_override_enable_then_disable(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_id: str,
) -> None:
    client, workspace_id = admin_client

    install_resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_id}/install",
        json={
            "auth_method": "static",
            "credential_plaintext": "ghp_test",
        },
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["install_id"]

    # Default: workspace does NOT inherit org-wide install (invisible by default).
    list_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers")
    assert list_resp.status_code == 200
    inherited_ids = {item["id"] for item in list_resp.json()["inherited"]}
    assert install_id not in inherited_ids

    # Catalog list reflects that it's not visible.
    catalog_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    items = {item["slug"]: item for item in catalog_resp.json()["items"]}
    assert items["github"]["org_install_id"] == install_id
    assert items["github"]["workspace_visible"] is False

    # Enable for this workspace via the canonical override URL.
    enable_resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/org-installs/{install_id}/override",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 204, enable_resp.text

    list_after_enable = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers")
    inherited_ids = {item["id"] for item in list_after_enable.json()["inherited"]}
    assert install_id in inherited_ids

    # Catalog list now shows visible.
    catalog_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    items = {item["slug"]: item for item in catalog_resp.json()["items"]}
    assert items["github"]["org_install_id"] == install_id
    assert items["github"]["workspace_visible"] is True

    # Disable: deletes the override row, install disappears.
    disable_resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/org-installs/{install_id}/override",
        json={"enabled": False},
    )
    assert disable_resp.status_code == 204, disable_resp.text

    list_after_disable = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers")
    inherited_ids = {item["id"] for item in list_after_disable.json()["inherited"]}
    assert install_id not in inherited_ids


async def test_workspace_override_rejects_workspace_private_install(
    member_client: tuple[httpx.AsyncClient, str],
    catalog_id: str,
) -> None:
    """Override is meaningless for workspace-private installs → 400."""
    client, workspace_id = member_client

    install_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/catalog/{catalog_id}/install",
        json={"auth_method": "static", "credential_plaintext": "x"},
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["install_id"]

    resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/org-installs/{install_id}/override",
        json={"enabled": False},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "mcp_catalog.workspace_owned_no_override"


async def test_workspace_override_unknown_install_returns_404(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, workspace_id = admin_client
    resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/org-installs/mcp-nope/override",
        json={"enabled": False},
    )
    assert resp.status_code == 404
