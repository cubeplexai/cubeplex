"""E2E tests for workspace MCP catalog routes (Task 10).

Covers:
  GET  /ws/{ws}/mcp/catalog                         → WorkspaceCatalogListOut
  PUT  /ws/{ws}/mcp/templates/{template_id}/state   → WorkspaceCatalogRowOut
  POST /ws/{ws}/mcp/templates                       → MCPTemplateOut (201)
  POST /ws/{ws}/mcp/templates/{template_id}/promote → MCPTemplateOut

Tests:
  1. catalog shows visible templates with enabled state
  2. lazy-enable creates a shared connector (spec test #1 HTTP half)
  3. enable rejected when org disabled (spec test #3)
  4. ws-custom template invisible to sibling workspace (spec test #6 pre-promote)
  5. promote makes template enableable by sibling (spec test #6)
  6. mixed grants: oauth user + static workspace (spec test #4)
  7. non-admin member PUT .../state → 403
  8. workspace deletion cascades and purges unpromoted ws templates (active)
  9. workspace deletion cascades non-active ws templates (FK guard)
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url

pytestmark = pytest.mark.usefixtures("stub_discover_tools")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Direct async_sessionmaker for DB-state assertions."""
    eng = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    finally:
        await eng.dispose()


async def _get_ws_id(client: httpx.AsyncClient) -> str:
    resp = await client.get("/api/v1/workspaces")
    assert resp.status_code == 200, resp.text
    return resp.json()[0]["id"]


async def _get_org_id(client: httpx.AsyncClient) -> str:
    resp = await client.get("/api/v1/workspaces")
    assert resp.status_code == 200, resp.text
    return resp.json()[0]["org_id"]


# ---------------------------------------------------------------------------
# Test 1: catalog shows visible templates with enabled state
# ---------------------------------------------------------------------------


async def test_ws_catalog_shows_visible_templates_with_enabled_state(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
) -> None:
    """GET /ws/{ws}/mcp/catalog returns WorkspaceCatalogListOut.

    After distribute (which creates a connector + state row for the workspace),
    the catalog must include the template with enabled=True.
    Before distribute, the template appears but with enabled=False (no connector).
    """
    client, workspace_id = admin_client

    # Catalog before distribute — template is visible but no connector yet
    resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "items" in data
    items = data["items"]
    row = next((r for r in items if r["template"]["template_id"] == noauth_template_id), None)
    assert row is not None, "noauth template must appear in workspace catalog"
    assert row["enabled"] is False
    assert row["connector"] is None
    assert row["usable"] is None

    # Distribute → connector + state row created, enabled=True
    dist = await client.post(
        f"/api/v1/admin/mcp/templates/{noauth_template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist.status_code == 200, dist.text

    # Catalog after distribute — now enabled
    resp2 = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert resp2.status_code == 200, resp2.text
    row2 = next(
        (r for r in resp2.json()["items"] if r["template"]["template_id"] == noauth_template_id),
        None,
    )
    assert row2 is not None
    assert row2["enabled"] is True
    assert row2["connector"] is not None
    connector_id = row2["connector"]["connector_id"]
    assert connector_id != ""
    # No-auth connector is usable (credential_policy='none')
    assert row2["usable"] is True


# ---------------------------------------------------------------------------
# Test 2: lazy-enable creates shared connector (spec test #1 HTTP half)
# ---------------------------------------------------------------------------


async def test_lazy_enable_creates_shared_connector(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """PUT .../state with enabled=True lazily materialises the connector.

    The connector must be shared (org-scoped MCPConnector with template_id),
    not a workspace-private install.
    """
    client, workspace_id = admin_client

    # Before enable: catalog shows no connector
    cat_before = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert cat_before.status_code == 200, cat_before.text
    row_before = next(
        (
            r
            for r in cat_before.json()["items"]
            if r["template"]["template_id"] == noauth_template_id
        ),
        None,
    )
    assert row_before is not None
    assert row_before["connector"] is None

    # Lazy-enable via PUT .../state
    resp = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/templates/{noauth_template_id}/state",
        json={"enabled": True},
    )
    assert resp.status_code == 200, resp.text
    row_out = resp.json()
    assert row_out["enabled"] is True
    assert row_out["template"]["template_id"] == noauth_template_id
    assert row_out["connector"] is not None
    connector_id = row_out["connector"]["connector_id"]

    # Connector must exist in DB and belong to the org (shared)
    from cubebox.models import MCPConnector

    async with db_maker() as session:
        connector = await session.get(MCPConnector, connector_id)
        assert connector is not None
        assert connector.template_id == noauth_template_id
        assert connector.status == "active"

    # Catalog after enable shows enabled=True
    cat_after = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert cat_after.status_code == 200, cat_after.text
    row_after = next(
        (
            r
            for r in cat_after.json()["items"]
            if r["template"]["template_id"] == noauth_template_id
        ),
        None,
    )
    assert row_after is not None
    assert row_after["enabled"] is True


# ---------------------------------------------------------------------------
# Test 3: enable rejected when org disabled (spec test #3)
# ---------------------------------------------------------------------------


async def test_enable_rejected_when_org_disabled(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
) -> None:
    """PUT .../state 409s with template_disabled_in_org when org admin disabled it."""
    client, workspace_id = admin_client

    # Org admin disables the template
    disable_resp = await client.put(
        f"/api/v1/admin/mcp/templates/{noauth_template_id}/disable",
    )
    assert disable_resp.status_code == 204, disable_resp.text

    # Catalog must not show this template (org-disabled templates are excluded)
    cat = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert cat.status_code == 200, cat.text
    items = cat.json()["items"]
    row = next((r for r in items if r["template"]["template_id"] == noauth_template_id), None)
    assert row is None, "org-disabled template must be excluded from workspace catalog"

    # PUT .../state must 409 template_disabled_in_org
    enable_resp = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/templates/{noauth_template_id}/state",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 409, enable_resp.text
    assert enable_resp.json()["detail"]["code"] == "template_disabled_in_org"

    # PUT with an unknown template ID → 404 template_not_visible
    enable_unknown = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/templates/nonexistent-tid/state",
        json={"enabled": True},
    )
    assert enable_unknown.status_code == 404, enable_unknown.text
    assert enable_unknown.json()["detail"]["code"] == "template_not_visible"


# ---------------------------------------------------------------------------
# Test 4: ws-custom template invisible to sibling workspace (spec test #6 pre-promote)
# ---------------------------------------------------------------------------


async def test_ws_custom_template_invisible_to_sibling_workspace(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """POST /ws/{ws}/mcp/templates creates a scope='workspace' template.

    That template must NOT be visible in a sibling workspace's catalog.
    """
    client, workspace_id = admin_client

    # Create sibling workspace
    org_id = await _get_org_id(client)
    sib = await client.post(
        "/api/v1/workspaces",
        json={"name": "sib-ws-scope-test", "org_id": org_id},
    )
    assert sib.status_code == 201, sib.text
    sibling_ws_id = sib.json()["id"]

    # Create workspace-custom template in our workspace
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/templates",
        json={
            "name": "My Custom WS MCP",
            "server_url": "https://custom-ws.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["scope"] == "workspace"
    assert created["workspace_id"] == workspace_id
    template_id = created["template_id"]

    # Own workspace catalog should show the template
    own_cat = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert own_cat.status_code == 200, own_cat.text
    own_items = own_cat.json()["items"]
    own_row = next((r for r in own_items if r["template"]["template_id"] == template_id), None)
    assert own_row is not None, "workspace-custom template must appear in its own workspace catalog"

    # Sibling workspace catalog must NOT show the template
    sib_cat = await client.get(f"/api/v1/ws/{sibling_ws_id}/mcp/catalog")
    assert sib_cat.status_code == 200, sib_cat.text
    sib_items = sib_cat.json()["items"]
    sib_row = next((r for r in sib_items if r["template"]["template_id"] == template_id), None)
    assert sib_row is None, "ws-custom template must NOT be visible in sibling workspace"


# ---------------------------------------------------------------------------
# Test 5: promote makes template enableable by sibling (spec test #6)
# ---------------------------------------------------------------------------


async def test_promote_makes_template_enableable_by_sibling(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """After promote, template scope becomes 'org' and sibling can enable it."""
    client, workspace_id = admin_client

    # Create sibling workspace
    org_id = await _get_org_id(client)
    sib = await client.post(
        "/api/v1/workspaces",
        json={"name": "sib-ws-promote-test", "org_id": org_id},
    )
    assert sib.status_code == 201, sib.text
    sibling_ws_id = sib.json()["id"]

    # Create workspace-custom template
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/templates",
        json={
            "name": "Promotable WS MCP",
            "server_url": "https://promotable-ws.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert resp.status_code == 201, resp.text
    template_id = resp.json()["template_id"]

    # Sibling workspace cannot see it yet
    sib_cat_before = await client.get(f"/api/v1/ws/{sibling_ws_id}/mcp/catalog")
    assert sib_cat_before.status_code == 200, sib_cat_before.text
    sib_row_before = next(
        (r for r in sib_cat_before.json()["items"] if r["template"]["template_id"] == template_id),
        None,
    )
    assert sib_row_before is None

    # Promote from owner workspace
    promote_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/templates/{template_id}/promote",
    )
    assert promote_resp.status_code == 200, promote_resp.text
    promoted = promote_resp.json()
    assert promoted["scope"] == "org"
    assert promoted["workspace_id"] is None

    # Promoting from sibling (not owner) must 404
    promote_sib = await client.post(
        f"/api/v1/ws/{sibling_ws_id}/mcp/templates/{template_id}/promote",
    )
    assert promote_sib.status_code == 404, promote_sib.text

    # Sibling can now see and enable the template
    sib_cat_after = await client.get(f"/api/v1/ws/{sibling_ws_id}/mcp/catalog")
    assert sib_cat_after.status_code == 200, sib_cat_after.text
    sib_row_after = next(
        (r for r in sib_cat_after.json()["items"] if r["template"]["template_id"] == template_id),
        None,
    )
    assert sib_row_after is not None, "after promote, sibling must see the org-scoped template"

    # Sibling can enable it
    enable_resp = await client.put(
        f"/api/v1/ws/{sibling_ws_id}/mcp/templates/{template_id}/state",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200, enable_resp.text
    assert enable_resp.json()["enabled"] is True


# ---------------------------------------------------------------------------
# Test 6: mixed grants — oauth user + static workspace (spec test #4)
# ---------------------------------------------------------------------------


async def test_mixed_grants_oauth_user_plus_static_workspace(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Catalog row shows correct credential_availability_by_scope for mixed grants.

    - Static workspace grant set via existing ws grant endpoint.
    - OAuth user grant seeded directly in DB (auth_method='oauth').
    - Both resolve in list_runtime_specs (usable=True when policy='workspace').
    """
    client, workspace_id = admin_client

    # Distribute to create connector + state row for our workspace
    dist = await client.post(
        f"/api/v1/admin/mcp/templates/{static_template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist.status_code == 200, dist.text
    connector_id = dist.json()["connector"]["connector_id"]

    # Set credential policy to 'workspace' via PUT .../state
    state_resp = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/templates/{static_template_id}/state",
        json={"enabled": True, "credential_policy": "workspace"},
    )
    assert state_resp.status_code == 200, state_resp.text

    # Create workspace grant via existing endpoint
    grant_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs/{connector_id}/grants/workspace",
        json={"credential_plaintext": "ws-static-token"},
    )
    assert grant_resp.status_code == 201, grant_resp.text

    # Seed an oauth user grant directly in DB

    from cubebox.models import Credential, MCPCredentialGrant, Workspace

    async with db_maker() as session:
        ws_row = await session.get(Workspace, workspace_id)
        assert ws_row is not None
        org_id = ws_row.org_id

        # Seed a fake credential + oauth user grant
        cred = Credential(
            org_id=org_id,
            kind="mcp_server",
            name=f"oauth-user-grant-{secrets.token_hex(4)}",
            value_encrypted=b"fake-oauth-token",
        )
        session.add(cred)
        await session.flush()

        # Get user id from workspace membership
        from sqlalchemy import select

        from cubebox.models import Membership

        mem = (
            (
                await session.execute(
                    select(Membership).where(
                        Membership.workspace_id == workspace_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .first()
        )
        assert mem is not None
        user_id = mem.user_id

        oauth_grant = MCPCredentialGrant(
            org_id=org_id,
            connector_id=connector_id,
            grant_scope="user",
            auth_method="oauth",
            workspace_id=workspace_id,
            user_id=user_id,
            credential_id=cred.id,
            created_by_user_id=user_id,
        )
        session.add(oauth_grant)
        await session.commit()

    # Catalog row must show both workspace and user credential available
    cat = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert cat.status_code == 200, cat.text
    row = next(
        (r for r in cat.json()["items"] if r["template"]["template_id"] == static_template_id),
        None,
    )
    assert row is not None
    cred_avail = row["credential_availability_by_scope"]
    assert cred_avail["workspace"] is True, "workspace grant must be available"
    assert cred_avail["user"] is True, "seeded oauth user grant must be available"

    # With policy='workspace', the connector is usable
    assert row["usable"] is True

    # list_runtime_specs includes this connector (it's usable)
    # We verify by checking the effective connector list
    effective = await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")
    assert effective.status_code == 200, effective.text
    eff_items = effective.json()["items"]
    eff_row = next((r for r in eff_items if r["install"]["connector_id"] == connector_id), None)
    assert eff_row is not None
    assert eff_row["usable"] is True


# ---------------------------------------------------------------------------
# Test 7: non-admin member PUT .../state → 403
# ---------------------------------------------------------------------------


async def test_non_admin_member_put_state_is_403(
    member_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
) -> None:
    """PUT .../state requires workspace admin; plain member gets 403."""
    client, workspace_id = member_client

    resp = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/templates/{noauth_template_id}/state",
        json={"enabled": True},
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Test 8: workspace deletion cascades unpromoted ws templates
# ---------------------------------------------------------------------------


async def test_workspace_deletion_purges_unpromoted_ws_templates(
    admin_client: tuple[httpx.AsyncClient, str],
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """DELETE /workspaces/{ws} purges unpromoted scope='workspace' templates.

    After deletion, the template row should be either deleted or marked
    status='deleted'. The connector (if one was created) must also be gone.
    """
    client, workspace_id = admin_client

    # Create a second workspace to keep (can't delete last workspace)
    org_id = await _get_org_id(client)
    keep_ws_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "keep-ws-cascade-test", "org_id": org_id},
    )
    assert keep_ws_resp.status_code == 201, keep_ws_resp.text

    # Create a ws-custom template in our workspace
    tpl_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/templates",
        json={
            "name": "Cascade Delete Test MCP",
            "server_url": "https://cascade-delete-test.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert tpl_resp.status_code == 201, tpl_resp.text
    template_id = tpl_resp.json()["template_id"]

    # Lazy-enable it (creates a connector)
    enable_resp = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/templates/{template_id}/state",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200, enable_resp.text
    connector_id = enable_resp.json()["connector"]["connector_id"]

    # Delete the workspace
    del_resp = await client.delete(f"/api/v1/workspaces/{workspace_id}")
    assert del_resp.status_code == 204, del_resp.text

    # Verify template and connector are gone/deleted
    from cubebox.models import MCPConnector, MCPConnectorTemplate

    async with db_maker() as session:
        tpl = await session.get(MCPConnectorTemplate, template_id)
        # Template should be deleted or have status='deleted'
        assert tpl is None or tpl.status == "deleted", (
            f"template must be deleted after workspace deletion, got status={tpl.status if tpl else 'None'}"
        )

        connector = await session.get(MCPConnector, connector_id)
        # Connector should be purged (hard-deleted)
        assert connector is None or connector.status != "active", (
            "connector must be purged after workspace deletion"
        )


# ---------------------------------------------------------------------------
# Test 9: workspace deletion cascades non-active ws templates (FK guard)
# ---------------------------------------------------------------------------


async def test_workspace_deletion_cascades_non_active_ws_templates(
    admin_client: tuple[httpx.AsyncClient, str],
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """DELETE /workspaces/{ws} must also clear workspace_id on non-active templates.

    Regression guard: before the fix, only 'active' templates were collected for
    the cascade update.  A template whose status was set to 'deleted' still held
    workspace_id=<ws>, causing a FK violation on workspace deletion.
    """
    from sqlalchemy import update

    from cubebox.models import MCPConnectorTemplate

    client, workspace_id = admin_client

    # Need a second workspace so the first can be deleted.
    org_id = await _get_org_id(client)
    keep_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "keep-ws-nonactive-test", "org_id": org_id},
    )
    assert keep_resp.status_code == 201, keep_resp.text

    # Create a workspace-scoped template (status='active' initially).
    tpl_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/templates",
        json={
            "name": "Non-active Cascade Test MCP",
            "server_url": "https://nonactive-cascade.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert tpl_resp.status_code == 201, tpl_resp.text
    template_id = tpl_resp.json()["template_id"]

    # Force the template to a non-active status directly in the DB.
    async with db_maker() as session:
        await session.execute(
            update(MCPConnectorTemplate)
            .where(MCPConnectorTemplate.id == template_id)  # type: ignore[arg-type]
            .values(status="deleted")
        )
        await session.commit()

    # Delete the workspace — must succeed (204) with no FK violation.
    del_resp = await client.delete(f"/api/v1/workspaces/{workspace_id}")
    assert del_resp.status_code == 204, (
        f"workspace deletion must succeed even with non-active templates; got {del_resp.status_code}: {del_resp.text}"
    )

    # Confirm the template no longer references the deleted workspace.
    async with db_maker() as session:
        tpl = await session.get(MCPConnectorTemplate, template_id)
        assert tpl is None or tpl.workspace_id is None, (
            f"non-active template must have workspace_id cleared after deletion, got {tpl.workspace_id if tpl else 'row missing'}"
        )


# ---------------------------------------------------------------------------
# Test 10: ws grant routes honour org-disable veto (F3)
# ---------------------------------------------------------------------------


async def test_ws_grant_blocked_when_template_disabled(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
) -> None:
    """POST /ws/{ws}/mcp/installs/{cid}/grants/me returns 409 template_disabled_in_org
    after the org admin disables the template.

    Covers the ws-side disable veto that R1 F2 missed: grants/me and grants/workspace
    as well as their oauth/start equivalents all call
    _reject_ws_grant_if_template_disabled before doing any credential work.
    """
    client, workspace_id = admin_client

    # Enable the static template in this workspace (lazy-enable creates the connector)
    enable_resp = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/templates/{static_template_id}/state",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200, enable_resp.text
    connector_id = enable_resp.json()["connector"]["connector_id"]
    assert connector_id, "connector must be created"

    # Org admin disables the template
    disable_resp = await client.put(
        f"/api/v1/admin/mcp/templates/{static_template_id}/disable",
    )
    assert disable_resp.status_code == 204, disable_resp.text

    # POST grants/me must now 409
    grant_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs/{connector_id}/grants/me",
        json={"credential_plaintext": "my-token"},
    )
    assert grant_resp.status_code == 409, grant_resp.text
    assert grant_resp.json()["detail"]["code"] == "template_disabled_in_org"
