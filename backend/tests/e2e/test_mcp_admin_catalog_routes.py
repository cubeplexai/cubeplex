"""E2E tests for admin catalog / template routes (Task 9 of mcp-template-centric).

Six named tests covering the admin catalog HTTP surface:

  1. test_catalog_lists_every_visible_template_with_facts
  2. test_distribute_does_not_resurrect_explicitly_disabled_workspace
  3. test_disable_hides_from_workspace_and_rejects_enable  (route half)
  4. test_purge_then_reenable_from_zero
  5. test_create_org_template_and_grant_flow
  6. test_admin_catalog_needs_attention_on_expired_grant

Task 10 (workspace routes) will add the workspace-route rejection half of
test_disable; see the comment inside test_disable_hides_from_workspace_and_rejects_enable.
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
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Direct async_sessionmaker for DB-state assertions."""
    eng = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    finally:
        await eng.dispose()


async def _seed_global_template(
    *,
    slug: str,
    name: str,
    auth_method: str = "none",
    credential_policy: str = "none",
) -> str:
    """Insert a global-scope template directly into the DB (bypasses org check)."""
    from tests.e2e.conftest import _seed_four_layer_template

    return await _seed_four_layer_template(
        slug=slug,
        name=name,
        supported_auth_methods=[auth_method],
        default_credential_policy=credential_policy,
    )


async def _get_catalog(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get("/api/v1/admin/mcp/catalog")
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _find_row(rows: list[dict], template_id: str) -> dict | None:
    for r in rows:
        if r["template"]["template_id"] == template_id:
            return r
    return None


# ---------------------------------------------------------------------------
# Test 1 — catalog lists every visible template with facts
# ---------------------------------------------------------------------------


async def test_catalog_lists_every_visible_template_with_facts(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """GET /admin/mcp/catalog: seeded global template appears with connector=None;
    after distribute it appears with in_use=True and enabled_workspace_count matching
    the workspace count."""
    client, workspace_id = admin_client

    suffix = secrets.token_hex(4)
    template_id = await _seed_global_template(
        slug=f"catalog-test-{suffix}",
        name=f"Catalog Test {suffix}",
    )

    rows = await _get_catalog(client)
    row = _find_row(rows, template_id)
    assert row is not None, "seeded template must appear in catalog"
    assert row["connector"] is None, "no connector before distribute"
    assert row["in_use"] is False
    assert row["disabled"] is False
    assert isinstance(row["eligible_workspace_count"], int)
    assert row["org_grant_status"] is None

    # Distribute → connector should materialise
    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": True},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    dist_row = dist_resp.json()
    assert dist_row["in_use"] is True
    assert dist_row["connector"] is not None
    connector_id = dist_row["connector"]["connector_id"]
    assert connector_id.startswith("mcpco-")

    # Catalog refreshed
    rows2 = await _get_catalog(client)
    row2 = _find_row(rows2, template_id)
    assert row2 is not None
    assert row2["in_use"] is True
    assert row2["enabled_workspace_count"] >= 1
    assert row2["eligible_workspace_count"] >= 1


# ---------------------------------------------------------------------------
# Test 2 — distribute does NOT resurrect an explicitly disabled workspace
# ---------------------------------------------------------------------------


async def test_distribute_does_not_resurrect_explicitly_disabled_workspace(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Spec test #2: if a workspace admin explicitly disabled the connector,
    a re-distribute must not flip it back to enabled.

    Setup path (ws routes still broken until Task 10 — use service directly):
    We distribute to all workspaces (enable_existing=True), then manually
    set the state row to disabled=False via set_workspace_enabled service,
    then distribute again with enable_existing=True — the previously-disabled
    workspace must not appear in the enabled count.

    Since ws routes are broken we seed the disabled state row via the DB.
    """
    client, workspace_id = admin_client

    suffix = secrets.token_hex(4)
    template_id = await _seed_global_template(
        slug=f"no-resurrect-{suffix}",
        name=f"No Resurrect {suffix}",
    )

    # First distribute — enables all workspaces (including our test workspace)
    dist1 = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist1.status_code == 200, dist1.text
    connector_id = dist1.json()["connector"]["connector_id"]

    # Manually set our workspace's state to disabled via the DB
    # (simulates a workspace admin clicking "disable").
    from cubebox.repositories.mcp import MCPWorkspaceConnectorStateRepository

    eng = create_async_engine(_build_database_url(), poolclass=NullPool)
    sm = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sm() as sess:
            # Resolve org_id from the workspace
            from cubebox.repositories.workspace import WorkspaceRepository

            ws_repo = WorkspaceRepository(sess)
            ws = await ws_repo.get(workspace_id)
            assert ws is not None
            org_id = ws.org_id

            state_repo = MCPWorkspaceConnectorStateRepository(sess, org_id=org_id)
            # Get the existing state row and flip it to disabled
            existing = await state_repo.get(workspace_id, connector_id)
            if existing is not None:
                existing.enabled = False
                existing.enablement_source = "workspace_manual"
                await sess.commit()
    finally:
        await eng.dispose()

    # Re-distribute with enable_existing=True — must NOT re-enable our ws
    dist2 = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist2.status_code == 200, dist2.text
    # The disabled workspace should not be counted as enabled
    # (the spec says: workspaces that already have a state row are never touched)
    row2 = dist2.json()
    # enabled_workspace_count must be 0 (all existing rows were already present)
    assert row2["enabled_workspace_count"] == 0, (
        f"explicitly-disabled workspace must not be re-enabled; row={row2}"
    )


# ---------------------------------------------------------------------------
# Test 3 — disable hides from workspace catalog + rejects re-enable
# ---------------------------------------------------------------------------


async def test_disable_hides_from_workspace_and_rejects_enable(
    admin_client: tuple[httpx.AsyncClient, str],
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Spec test #3 (route half):

    PUT /admin/mcp/templates/{id}/disable → settings row disabled=True in DB
    + a subsequent GET /admin/mcp/catalog shows disabled: true for that template.

    The workspace-route rejection assertion (workspace catalog hides disabled
    templates, set_workspace_enabled rejects them) is deferred to Task 10
    (ws routes are still broken). See TODO comment in test_mcp_workspace_catalog_routes.py.
    """
    client, workspace_id = admin_client

    suffix = secrets.token_hex(4)
    template_id = await _seed_global_template(
        slug=f"disable-test-{suffix}",
        name=f"Disable Test {suffix}",
    )

    # Verify not disabled initially
    rows_before = await _get_catalog(client)
    row_before = _find_row(rows_before, template_id)
    assert row_before is not None
    assert row_before["disabled"] is False

    # Disable it
    disable_resp = await client.put(f"/api/v1/admin/mcp/templates/{template_id}/disable")
    assert disable_resp.status_code == 204, disable_resp.text

    # Catalog now shows disabled=True
    rows_after = await _get_catalog(client)
    row_after = _find_row(rows_after, template_id)
    assert row_after is not None, "disabled template still appears in admin catalog"
    assert row_after["disabled"] is True

    # DB-level: settings row exists with disabled=True
    from cubebox.repositories.mcp import MCPTemplateSettingsRepository

    async with db_maker() as sess:
        # Need to get the org_id
        from cubebox.repositories.workspace import WorkspaceRepository

        ws_repo = WorkspaceRepository(sess)
        ws = await ws_repo.get(workspace_id)
        assert ws is not None
        org_id = ws.org_id
        settings_repo = MCPTemplateSettingsRepository(sess, org_id=org_id)
        disabled_ids = await settings_repo.disabled_template_ids()
        assert template_id in disabled_ids, "settings repo must report template as disabled"

    # Re-enable via DELETE /disable
    reenable_resp = await client.delete(f"/api/v1/admin/mcp/templates/{template_id}/disable")
    assert reenable_resp.status_code == 204, reenable_resp.text

    # Catalog shows disabled=False again
    rows_final = await _get_catalog(client)
    row_final = _find_row(rows_final, template_id)
    assert row_final is not None
    assert row_final["disabled"] is False

    # TODO(Task 10): assert ws catalog hides disabled templates and
    # set_workspace_enabled rejects them at the ws route layer.


# ---------------------------------------------------------------------------
# Test 4 — purge then re-enable from zero
# ---------------------------------------------------------------------------


async def test_purge_then_reenable_from_zero(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Spec test #5: distribute → purge → catalog shows connector=None;
    can distribute again without error (fresh connector created).
    """
    client, workspace_id = admin_client

    suffix = secrets.token_hex(4)
    template_id = await _seed_global_template(
        slug=f"purge-reenable-{suffix}",
        name=f"Purge Reenable {suffix}",
    )

    # Distribute → connector materialises
    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    assert dist_resp.json()["in_use"] is True

    # Purge
    purge_resp = await client.post(f"/api/v1/admin/mcp/templates/{template_id}/purge")
    assert purge_resp.status_code == 204, purge_resp.text

    # Catalog: connector=None after purge
    rows_mid = await _get_catalog(client)
    row_mid = _find_row(rows_mid, template_id)
    assert row_mid is not None
    assert row_mid["in_use"] is False
    assert row_mid["connector"] is None

    # Re-distribute → connector re-created
    dist2_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": True},
    )
    assert dist2_resp.status_code == 200, dist2_resp.text
    assert dist2_resp.json()["in_use"] is True
    assert dist2_resp.json()["connector"] is not None


# ---------------------------------------------------------------------------
# Test 5 — create org template and grant flow
# ---------------------------------------------------------------------------


async def test_create_org_template_and_grant_flow(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """POST /admin/mcp/templates (org scope) → distribute →
    POST /admin/mcp/installs/{id}/grants/org (static) →
    catalog row org_grant_status == 'valid'.
    """
    client, workspace_id = admin_client

    suffix = secrets.token_hex(4)

    # Create org-scoped template via the new endpoint
    create_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"Org Custom {suffix}",
            "server_url": f"https://custom-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "static",
            "default_credential_policy": "org",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    template_id = created["template_id"]
    assert created["scope"] == "org"

    # Distribute → connector created
    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    # Create org grant via existing grants endpoint (connector_id-keyed, unchanged)
    grant_resp = await client.post(
        f"/api/v1/admin/mcp/installs/{connector_id}/grants/org",
        json={"credential_plaintext": "my-static-token"},
    )
    assert grant_resp.status_code == 201, grant_resp.text

    # Catalog row must now show org_grant_status='valid' and connector.org_grant_auth_method='static'
    rows = await _get_catalog(client)
    row = _find_row(rows, template_id)
    assert row is not None
    assert row["org_grant_status"] == "valid"
    assert row["in_use"] is True
    assert row["connector"]["org_grant_auth_method"] == "static"


# ---------------------------------------------------------------------------
# Test 6 — needs_attention on expired grant
# ---------------------------------------------------------------------------


async def test_admin_catalog_needs_attention_on_expired_grant(
    admin_client: tuple[httpx.AsyncClient, str],
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Spec test #7: catalog row shows needs_attention=True + org_grant_status='expired'
    when the org grant is in expired state.

    We seed the grant directly in the DB (setting grant_status='expired')
    since there's no API to force expiry without a real OAuth flow.
    """
    client, workspace_id = admin_client

    suffix = secrets.token_hex(4)

    # Create a static org template + distribute
    create_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"Attention Test {suffix}",
            "server_url": f"https://attention-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "static",
            "default_credential_policy": "org",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    template_id = create_resp.json()["template_id"]

    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": False, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    # Create a valid grant via API, then force it to expired via DB
    grant_resp = await client.post(
        f"/api/v1/admin/mcp/installs/{connector_id}/grants/org",
        json={"credential_plaintext": "will-expire"},
    )
    assert grant_resp.status_code == 201, grant_resp.text

    # Force grant_status to 'expired' in DB
    async with db_maker() as sess:
        from sqlalchemy import select

        from cubebox.models.mcp import MCPCredentialGrant

        grant_row = (
            await sess.execute(
                select(MCPCredentialGrant).where(
                    MCPCredentialGrant.connector_id == connector_id,  # type: ignore[arg-type]
                    MCPCredentialGrant.grant_scope == "org",  # type: ignore[arg-type]
                )
            )
        ).scalar_one()
        grant_row.grant_status = "expired"
        await sess.commit()

    # Catalog row should reflect needs_attention=True
    rows = await _get_catalog(client)
    row = _find_row(rows, template_id)
    assert row is not None
    assert row["org_grant_status"] == "expired"
    assert row["needs_attention"] is True


# ---------------------------------------------------------------------------
# Test 7 — delete org-owned template (happy path + guards)
# ---------------------------------------------------------------------------


async def test_delete_org_template_happy_path(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """DELETE /admin/mcp/templates/{id} removes org-owned template with no connector."""
    client, workspace_id = admin_client

    suffix = secrets.token_hex(4)
    create_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"Delete Me {suffix}",
            "server_url": f"https://delete-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    template_id = create_resp.json()["template_id"]

    delete_resp = await client.delete(f"/api/v1/admin/mcp/templates/{template_id}")
    assert delete_resp.status_code == 204, delete_resp.text

    # Template no longer in catalog
    rows = await _get_catalog(client)
    assert _find_row(rows, template_id) is None


async def test_delete_global_template_returns_404(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """DELETE on a global-scope template returns 404 with template_not_owned_by_org."""
    client, workspace_id = admin_client
    suffix = secrets.token_hex(4)
    # Seed a global template
    global_tpl_id = await _seed_global_template(
        slug=f"global-del-{suffix}",
        name=f"Global Del {suffix}",
    )

    resp = await client.delete(f"/api/v1/admin/mcp/templates/{global_tpl_id}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "template_not_owned_by_org"


async def test_delete_template_in_use_returns_409(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """DELETE on a template that has an active connector returns 409 template_in_use."""
    client, workspace_id = admin_client

    suffix = secrets.token_hex(4)
    create_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"In Use {suffix}",
            "server_url": f"https://inuse-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    template_id = create_resp.json()["template_id"]

    # Distribute so a connector exists
    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": False, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text

    # Now delete should 409
    delete_resp = await client.delete(f"/api/v1/admin/mcp/templates/{template_id}")
    assert delete_resp.status_code == 409, delete_resp.text
    assert delete_resp.json()["detail"]["code"] == "template_in_use"


# ---------------------------------------------------------------------------
# F1: static grant rejected (422) when template only supports OAuth
# ---------------------------------------------------------------------------


async def test_static_grant_rejected_for_oauth_only_template(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """POSTing a static grant against an oauth-only template returns 422 auth_method_not_supported_by_template."""
    client, _workspace_id = admin_client

    suffix = secrets.token_hex(4)
    # Create an org-scoped oauth-only template and distribute it.
    from tests.e2e.conftest import _seed_four_layer_template

    template_id = await _seed_four_layer_template(
        slug=f"oauth-only-{suffix}",
        name=f"OAuth Only {suffix}",
        supported_auth_methods=["oauth"],
        default_credential_policy="org",
    )

    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": False, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    # Attempt static grant — must be rejected with 422
    grant_resp = await client.post(
        f"/api/v1/admin/mcp/installs/{connector_id}/grants/org",
        json={"credential_plaintext": "some-token"},
    )
    assert grant_resp.status_code == 422, grant_resp.text
    assert grant_resp.json()["detail"]["code"] == "auth_method_not_supported_by_template"


# ---------------------------------------------------------------------------
# F2: connector-keyed admin actions vetoed when template is org-disabled
# ---------------------------------------------------------------------------


async def test_invoke_tool_vetoed_when_template_disabled(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """admin_invoke_tool returns 409 template_disabled_in_org after PUT /disable."""
    client, _workspace_id = admin_client

    suffix = secrets.token_hex(4)
    create_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"Disable Veto {suffix}",
            "server_url": f"https://disable-veto-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    template_id = create_resp.json()["template_id"]

    # Distribute to get a connector_id
    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": False, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    # Disable the template
    disable_resp = await client.put(f"/api/v1/admin/mcp/templates/{template_id}/disable")
    assert disable_resp.status_code == 204, disable_resp.text

    # admin_invoke_tool must veto with 409 template_disabled_in_org
    invoke_resp = await client.post(
        f"/api/v1/admin/mcp/installs/{connector_id}/tools/some_tool/invoke",
        json={"arguments": {}},
    )
    assert invoke_resp.status_code == 409, invoke_resp.text
    assert invoke_resp.json()["detail"]["code"] == "template_disabled_in_org"
