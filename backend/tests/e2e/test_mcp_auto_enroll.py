"""E2E: admin install ``auto_enable_workspaces`` flag + new-workspace inheritance.

Covers:

- Default ``auto_enable_workspaces=True`` upserts an enabled
  ``WorkspaceMCPOverride`` for every existing workspace in the org → catalog
  list reports ``workspace_visible=True`` right after install.
- ``auto_enable_workspaces=False`` leaves overrides untouched →
  ``workspace_visible=False`` until each workspace explicitly enables.
- New workspaces created (via ``POST /api/v1/workspaces``) after an install
  inherit an enabled override iff that install's
  ``auto_enroll_new_workspaces`` column is True.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import MCPCatalogConnector
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository

pytestmark = pytest.mark.usefixtures("stub_discover_tools")


async def _seed_simple_connector(
    session: AsyncSession,
    *,
    slug: str = "github-ae",
    name: str = "GitHub AE",
) -> MCPCatalogConnector:
    repo = MCPCatalogConnectorRepository(session)
    row = await repo.upsert_by_slug(
        slug=slug,
        name=name,
        description=f"{name} test connector.",
        provider="GitHub",
        server_url=f"https://{slug}.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_scope="org",
        static_form_fields=[{"name": "token", "label": "API token", "secret": True}],
        static_auth_header_template="Bearer {token}",
        cred_metadata=None,
        status="active",
    )
    await session.commit()
    return row


@pytest_asyncio.fixture
async def catalog_one(db_session: AsyncSession) -> AsyncIterator[str]:
    row = await _seed_simple_connector(db_session)
    yield row.id


async def _org_id_for(client: httpx.AsyncClient, workspace_id: str) -> str:
    resp = await client.get("/api/v1/workspaces")
    assert resp.status_code == 200, resp.text
    for ws in resp.json():
        if ws["id"] == workspace_id:
            return ws["org_id"]
    raise AssertionError(f"workspace {workspace_id} not in listing")


async def _catalog_item(client: httpx.AsyncClient, workspace_id: str, slug: str) -> dict[str, Any]:
    resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert resp.status_code == 200, resp.text
    for item in resp.json()["items"]:
        if item["slug"] == slug:
            return item
    raise AssertionError(f"connector {slug} not in catalog response")


async def test_install_auto_enable_default_makes_existing_workspace_visible(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_one: str,
) -> None:
    client, workspace_id = admin_client

    resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_one}/install",
        json={"auth_method": "static", "credential_plaintext": "ghp_test"},
    )
    assert resp.status_code == 201, resp.text

    item = await _catalog_item(client, workspace_id, "github-ae")
    assert item["org_install_id"] is not None
    assert item["workspace_visible"] is True


async def test_install_auto_enable_false_keeps_existing_workspace_invisible(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_one: str,
) -> None:
    client, workspace_id = admin_client

    resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_one}/install",
        json={
            "auth_method": "static",
            "credential_plaintext": "ghp_test",
            "auto_enable_workspaces": False,
        },
    )
    assert resp.status_code == 201, resp.text

    item = await _catalog_item(client, workspace_id, "github-ae")
    assert item["org_install_id"] is not None
    assert item["workspace_visible"] is False


async def test_new_workspace_inherits_when_auto_enroll_true(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_one: str,
) -> None:
    client, original_ws_id = admin_client

    # Install with default auto_enable=true.
    install_resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_one}/install",
        json={"auth_method": "static", "credential_plaintext": "ghp_test"},
    )
    assert install_resp.status_code == 201, install_resp.text

    org_id = await _org_id_for(client, original_ws_id)

    create_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "second-ws", "org_id": org_id},
    )
    assert create_resp.status_code == 201, create_resp.text
    new_ws_id = create_resp.json()["id"]

    item = await _catalog_item(client, new_ws_id, "github-ae")
    assert item["workspace_visible"] is True, (
        "new workspace should inherit enabled override when auto_enroll=true"
    )


async def test_new_workspace_does_not_inherit_when_auto_enroll_false(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_one: str,
) -> None:
    client, original_ws_id = admin_client

    install_resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_one}/install",
        json={
            "auth_method": "static",
            "credential_plaintext": "ghp_test",
            "auto_enable_workspaces": False,
        },
    )
    assert install_resp.status_code == 201, install_resp.text

    org_id = await _org_id_for(client, original_ws_id)

    create_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "third-ws", "org_id": org_id},
    )
    assert create_resp.status_code == 201, create_resp.text
    new_ws_id = create_resp.json()["id"]

    item = await _catalog_item(client, new_ws_id, "github-ae")
    assert item["workspace_visible"] is False, (
        "new workspace must not inherit enabled override when auto_enroll=false"
    )


async def test_delete_install_clears_workspace_overrides(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_one: str,
) -> None:
    """Admin delete-install mirrors the auto_enable backfill: wipe overrides."""
    client, workspace_id = admin_client

    install_resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_one}/install",
        json={"auth_method": "static", "credential_plaintext": "ghp_test"},
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["install_id"]

    # Workspace inherits the auto-enable override.
    item = await _catalog_item(client, workspace_id, "github-ae")
    assert item["workspace_visible"] is True

    delete_resp = await client.delete(f"/api/v1/admin/mcp/installs/{install_id}")
    assert delete_resp.status_code == 204, delete_resp.text

    # After delete: install row survives but unauthed, and the override is gone
    # so workspace surfaces don't show a misleading "needs_setup" entry.
    item = await _catalog_item(client, workspace_id, "github-ae")
    assert item["org_install_id"] == install_id
    assert item["workspace_visible"] is False


async def test_deleted_install_does_not_zombie_into_new_workspaces(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_one: str,
) -> None:
    """Soft-deleted org install must not seep into workspaces created after delete.

    ``delete_install`` clears authed but keeps ``auto_enroll_new_workspaces=true``
    on the row (the flag is policy intent, not live state). The bootstrap helper
    therefore must gate by ``authed=true`` as well; otherwise the deleted
    install zombies back as a ``needs_setup`` entry in every fresh workspace.
    """
    client, original_ws_id = admin_client

    install_resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_one}/install",
        json={"auth_method": "static", "credential_plaintext": "ghp_test"},
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["install_id"]

    # Soft-delete the install.
    delete_resp = await client.delete(f"/api/v1/admin/mcp/installs/{install_id}")
    assert delete_resp.status_code == 204, delete_resp.text

    org_id = await _org_id_for(client, original_ws_id)

    create_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "fourth-ws", "org_id": org_id},
    )
    assert create_resp.status_code == 201, create_resp.text
    new_ws_id = create_resp.json()["id"]

    item = await _catalog_item(client, new_ws_id, "github-ae")
    assert item["workspace_visible"] is False, (
        "deleted-then-bootstrapped install must not appear as visible"
    )


async def test_switch_auth_method_preserves_workspace_overrides(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_one: str,
) -> None:
    """Rekey calls delete_install internally but must keep workspace visibility."""
    client, workspace_id = admin_client

    install_resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_one}/install",
        json={"auth_method": "static", "credential_plaintext": "ghp_test"},
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["install_id"]

    item = await _catalog_item(client, workspace_id, "github-ae")
    assert item["workspace_visible"] is True

    patch_resp = await client.patch(
        f"/api/v1/admin/mcp/installs/{install_id}",
        json={"auth_method": "static", "credential_plaintext": "ghp_rekeyed"},
    )
    assert patch_resp.status_code == 200, patch_resp.text

    # Override survives the rekey.
    item = await _catalog_item(client, workspace_id, "github-ae")
    assert item["workspace_visible"] is True, (
        "switch_auth_method must not revoke workspace visibility"
    )
