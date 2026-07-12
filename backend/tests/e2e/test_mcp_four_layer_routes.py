"""E2E coverage for the MCP four-layer route surface (rewritten for Task 9).

Original tests mapped 1:1 to spec scenarios; most have been migrated to the
template-centric surface (distribute/purge). Tests that depended on workspace
routes (ws_mcp) are deferred to Task 10 with a ``pytest.mark.skip`` and a
clear comment referencing the plan.

Invariants preserved after the rewrite:
  - Distribute fans out state rows to workspaces  (was: install fan-out)
  - Explicit workspace-disable is respected by re-distribute
  - Purge + re-distribute is idempotent (fresh connector created)
  - Org grant stays/goes independent of connector lifecycle
  - auto_enroll flag is correct per distribution mode
"""

from __future__ import annotations

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


async def _get_org_id(client: httpx.AsyncClient) -> str:
    resp = await client.get("/api/v1/workspaces")
    assert resp.status_code == 200, resp.text
    return resp.json()[0]["org_id"]


# ---------------------------------------------------------------------------
# Scenario #1 — org-scope distribute to selected workspace
# (was: test_org_admin_noauth_install_distributed_to_workspace_renders_usable)
# ---------------------------------------------------------------------------


async def test_distribute_to_selected_workspace_creates_state_row(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Distribute with enable_existing=True creates a state row for each workspace.

    The workspace-catalog view invariant (workspace sees usable connector) is
    deferred to Task 10 (ws routes); here we assert the DB invariant only.
    """
    client, workspace_id = admin_client

    # Create a sibling workspace
    org_id = await _get_org_id(client)
    sibling_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "sibling-ws-distribtest", "org_id": org_id},
    )
    assert sibling_resp.status_code == 201, sibling_resp.text
    sibling_ws_id = sibling_resp.json()["id"]

    # Distribute with enable_existing=True → creates state rows in ALL workspaces
    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{noauth_template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    dist_row = dist_resp.json()
    connector_id = dist_row["connector"]["connector_id"]

    # DB invariant: state rows exist for both workspaces
    from sqlalchemy import select

    from cubebox.models import MCPConnector, MCPWorkspaceConnectorState

    async with db_maker() as session:
        connector_row = await session.get(MCPConnector, connector_id)
        assert connector_row is not None
        assert connector_row.auto_enroll_new_workspaces is False

        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.connector_id == connector_id  # type: ignore[arg-type]
        )
        states = list((await session.execute(stmt)).scalars().all())
        ws_ids = {s.workspace_id for s in states}
        assert workspace_id in ws_ids, "targeted workspace must have state row"
        assert sibling_ws_id in ws_ids, "sibling gets state row when enable_existing=True"
        for s in states:
            if s.workspace_id in (workspace_id, sibling_ws_id):
                assert s.enabled is True

    # TODO(Task 10): assert workspace catalog shows usable for both workspaces.


# ---------------------------------------------------------------------------
# Scenario #2 — workspace-local install deferred to Task 10
# ---------------------------------------------------------------------------


async def test_workspace_local_noauth_install_renders_usable(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
) -> None:
    """Workspace admin enables a no-auth connector; it's immediately usable.

    Uses PUT /ws/{ws}/mcp/templates/{template_id}/state to enable and verifies
    the workspace catalog returns usable=True with no credential needed.
    """
    client, workspace_id = admin_client

    # Enable via ws state endpoint (lazy-creates connector + state row).
    enable_resp = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/templates/{noauth_template_id}/state",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 200, enable_resp.text
    row = enable_resp.json()
    assert row["enabled"] is True

    # Ws catalog should show the connector as usable.
    catalog_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert catalog_resp.status_code == 200, catalog_resp.text
    items = catalog_resp.json()["items"]
    enabled_row = next(
        (r for r in items if r["template"]["template_id"] == noauth_template_id), None
    )
    assert enabled_row is not None
    assert enabled_row["enabled"] is True
    assert enabled_row.get("usable") is True


# ---------------------------------------------------------------------------
# Scenario #3 — user-policy scope isolation (deferred to Task 10)
# ---------------------------------------------------------------------------


async def test_user_grant_policy_does_not_fall_back_to_org_grant(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
) -> None:
    """User-policy connector with an org grant but no user grant → usable=False.

    The static_template_id fixture seeds a template with credential_policy='user'.
    After distribute+org-grant, the workspace catalog must show usable=False
    because the effective policy is 'user' and no user grant is present.
    """
    client, workspace_id = admin_client

    # Distribute with user policy.
    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{static_template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    # Create an org-level grant (wrong scope for user policy).
    grant_resp = await client.post(
        f"/api/v1/admin/mcp/installs/{connector_id}/grants/org",
        json={"credential_plaintext": "org-token"},
    )
    assert grant_resp.status_code == 201, grant_resp.text

    # Ws catalog: credential_policy='user' → org grant alone is not enough.
    catalog_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert catalog_resp.status_code == 200, catalog_resp.text
    items = catalog_resp.json()["items"]
    row = next((r for r in items if r["template"]["template_id"] == static_template_id), None)
    assert row is not None
    assert row["enabled"] is True
    # user-policy connector with only an org grant → not usable
    assert row.get("usable") is False


# ---------------------------------------------------------------------------
# Scenario #4 — policy change drops previous-scope grant (deferred to Task 10)
# ---------------------------------------------------------------------------


async def test_policy_change_drops_previous_scope_grant_from_runtime(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
) -> None:
    """Switching workspace policy from org→user makes the existing org grant insufficient.

    1. Distribute with org policy → org grant → usable=True.
    2. PUT ws state with credential_policy='user' → org grant no longer satisfies.
    3. Ws catalog shows usable=False until a user grant is added.
    """
    client, workspace_id = admin_client

    # Distribute and set org policy explicitly.
    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{static_template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    # Override ws state to use org credential policy.
    enable_resp = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/templates/{static_template_id}/state",
        json={"enabled": True, "credential_policy": "org"},
    )
    assert enable_resp.status_code == 200, enable_resp.text

    # Add org grant → now usable=True.
    grant_resp = await client.post(
        f"/api/v1/admin/mcp/installs/{connector_id}/grants/org",
        json={"credential_plaintext": "org-token"},
    )
    assert grant_resp.status_code == 201, grant_resp.text

    catalog_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert catalog_resp.status_code == 200, catalog_resp.text
    items = catalog_resp.json()["items"]
    row = next((r for r in items if r["template"]["template_id"] == static_template_id), None)
    assert row is not None and row.get("usable") is True, (
        "With org policy + org grant, connector should be usable"
    )

    # Switch ws policy to user → org grant no longer sufficient.
    switch_resp = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/templates/{static_template_id}/state",
        json={"enabled": True, "credential_policy": "user"},
    )
    assert switch_resp.status_code == 200, switch_resp.text

    catalog_resp2 = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert catalog_resp2.status_code == 200, catalog_resp2.text
    items2 = catalog_resp2.json()["items"]
    row2 = next((r for r in items2 if r["template"]["template_id"] == static_template_id), None)
    assert row2 is not None and row2.get("usable") is False, (
        "After switching to user policy, org grant no longer makes connector usable"
    )


# ---------------------------------------------------------------------------
# Scenario #5 — disconnect keeps connector + state rows (grant delete is kept)
# ---------------------------------------------------------------------------


async def test_disconnect_keeps_connector_and_state(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Deleting an org grant leaves connector + workspace_state intact.

    The workspace-catalog usability assertion is deferred to Task 10.
    """
    client, workspace_id = admin_client

    # Distribute (creates connector + state row for our workspace)
    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{static_template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    # Create org grant
    grant_resp = await client.post(
        f"/api/v1/admin/mcp/installs/{connector_id}/grants/org",
        json={"credential_plaintext": "org-token"},
    )
    assert grant_resp.status_code == 201, grant_resp.text

    # Delete org grant
    disconnect = await client.delete(f"/api/v1/admin/mcp/installs/{connector_id}/grants/org")
    assert disconnect.status_code == 204, disconnect.text

    # DB invariants: connector + state rows survive disconnect
    from sqlalchemy import select

    from cubebox.models import MCPConnector, MCPWorkspaceConnectorState

    async with db_maker() as session:
        connector_row = await session.get(MCPConnector, connector_id)
        assert connector_row is not None
        assert connector_row.status == "active"

        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.connector_id == connector_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
        state = (await session.execute(stmt)).scalar_one_or_none()
        assert state is not None and state.enabled is True

    # Re-grant → catalog shows org_grant_status='valid' again
    regrant = await client.post(
        f"/api/v1/admin/mcp/installs/{connector_id}/grants/org",
        json={"credential_plaintext": "org-token-v2", "name": "regrant-v2"},
    )
    assert regrant.status_code == 201, regrant.text

    # Admin catalog should show valid grant
    catalog_resp = await client.get("/api/v1/admin/mcp/catalog")
    assert catalog_resp.status_code == 200, catalog_resp.text
    items = catalog_resp.json()["items"]
    row = next(
        (
            r
            for r in items
            if r.get("connector", {}) and r["connector"]["connector_id"] == connector_id
        ),
        None,
    )
    assert row is not None
    assert row["org_grant_status"] == "valid"

    # TODO(Task 10): assert ws catalog shows usable=True after re-grant.


# ---------------------------------------------------------------------------
# Regression: re-POST org grant must upsert (not collide)
# ---------------------------------------------------------------------------


async def test_repost_org_grant_is_idempotent(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
) -> None:
    client, workspace_id = admin_client

    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{static_template_id}/distribute",
        json={"enable_existing": False, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    first = await client.post(
        f"/api/v1/admin/mcp/installs/{connector_id}/grants/org",
        json={"credential_plaintext": "org-token-v1"},
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        f"/api/v1/admin/mcp/installs/{connector_id}/grants/org",
        json={"credential_plaintext": "org-token-v2"},
    )
    assert second.status_code == 201, second.text


# ---------------------------------------------------------------------------
# Scenario #6 — purge then redistribute same template
# (was: test_uninstall_then_reinstall_same_template)
# ---------------------------------------------------------------------------


async def test_purge_then_redistribute_same_template(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Purge a connector → connector gone; redistribute creates a fresh one."""
    client, workspace_id = admin_client

    # First distribute
    dist1 = await client.post(
        f"/api/v1/admin/mcp/templates/{noauth_template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist1.status_code == 200, dist1.text
    first_connector_id = dist1.json()["connector"]["connector_id"]

    # Purge
    purge = await client.post(f"/api/v1/admin/mcp/templates/{noauth_template_id}/purge")
    assert purge.status_code == 204, purge.text

    from cubebox.models import MCPConnector

    async with db_maker() as session:
        row = await session.get(MCPConnector, first_connector_id)
        # Connector should be gone (purge hard-deletes)
        assert row is None or row.status != "active"

    # Re-distribute → fresh connector created
    dist2 = await client.post(
        f"/api/v1/admin/mcp/templates/{noauth_template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": True},
    )
    assert dist2.status_code == 200, dist2.text
    second_connector_id = dist2.json()["connector"]["connector_id"]
    assert second_connector_id != first_connector_id

    # Catalog shows in_use=True
    catalog = await client.get("/api/v1/admin/mcp/catalog")
    assert catalog.status_code == 200
    items = catalog.json()["items"]
    row_out = next(
        (r for r in items if r["template"]["template_id"] == noauth_template_id),
        None,
    )
    assert row_out is not None
    assert row_out["in_use"] is True


# ---------------------------------------------------------------------------
# Scenario #7 — OAuth refresh (SKIPPED — same reason as before)
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="oauth_start_not_yet_wired_for_four_layer_grants")
async def test_oauth_refresh_before_runtime_returns_usable(
    admin_client: tuple[httpx.AsyncClient, str],
    oauth_template_id: str,
) -> None:
    """Re-enable once four-layer OAuth start is wired."""
    _client, _workspace_id = admin_client
    _ = oauth_template_id


# ---------------------------------------------------------------------------
# Scenario #8 — invalid credential policy rejected at schema boundary
# (was: test_invalid_credential_policy_rejected_at_api_boundary)
# ---------------------------------------------------------------------------


async def test_invalid_credential_policy_rejected_at_template_create(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """422 when creating a template with mismatched auth/policy."""
    client, _ws = admin_client

    # Literal rejection: "bogus" is not a valid policy
    bogus_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": "BogusPolicy",
            "server_url": "https://bogus.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "static",
            "default_credential_policy": "bogus",
        },
    )
    assert bogus_resp.status_code == 422, bogus_resp.text

    # Cross-field rejection: static auth + 'none' policy
    inconsistent_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": "InconsistentPolicy",
            "server_url": "https://inconsistent.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "static",
            "default_credential_policy": "none",
        },
    )
    assert inconsistent_resp.status_code == 422, inconsistent_resp.text


# ---------------------------------------------------------------------------
# Scenario #9 — PATCH connector state upserts (deferred to Task 10)
# ---------------------------------------------------------------------------


async def test_patch_state_upserts_for_org_install_with_no_state_row(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """PUT ws template state on a template with no prior state row creates the row.

    Uses PUT /ws/{ws}/mcp/templates/{template_id}/state (lazy-ensure path).
    """
    from sqlalchemy import select

    from cubebox.models import MCPWorkspaceConnectorState

    client, workspace_id = admin_client

    # Distribute without enabling (no state rows for this workspace yet).
    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{static_template_id}/distribute",
        json={"enable_existing": False, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    # Verify no state row exists for this workspace yet.
    async with db_maker() as session:
        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.connector_id == connector_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        assert existing is None, "no state row expected before PUT"

    # PUT state → lazy-creates state row.
    put_resp = await client.put(
        f"/api/v1/ws/{workspace_id}/mcp/templates/{static_template_id}/state",
        json={"enabled": True},
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["enabled"] is True

    # Verify state row was created.
    async with db_maker() as session:
        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.connector_id == connector_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
        state_row = (await session.execute(stmt)).scalar_one_or_none()
        assert state_row is not None
        assert state_row.enabled is True


# ---------------------------------------------------------------------------
# Scenario #10 — selected distribution does not auto-enroll new workspace
# ---------------------------------------------------------------------------


async def test_selected_distribution_does_not_auto_enroll_new_workspace(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Distribute with auto_enroll=False → auto_enroll_new_workspaces is False;
    new workspaces created later must not inherit the connector state.
    """
    import secrets as _secrets

    from sqlalchemy import select

    from cubebox.models import MCPConnector, MCPWorkspaceConnectorState

    client, workspace_id = admin_client
    org_id = await _get_org_id(client)

    # Use a fresh slug to avoid conflicts with test #1
    from tests.e2e.conftest import _seed_four_layer_template

    suffix = _secrets.token_hex(4)
    template_id = await _seed_four_layer_template(
        slug=f"auto-enroll-test-{suffix}",
        name=f"Auto Enroll Test {suffix}",
        supported_auth_methods=["none"],
        default_credential_policy="none",
    )

    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    # Persisted flag must be False
    async with db_maker() as session:
        connector_row = await session.get(MCPConnector, connector_id)
        assert connector_row is not None
        assert connector_row.auto_enroll_new_workspaces is False, (
            "auto_enroll=False in distribute must persist auto_enroll_new_workspaces=False"
        )

    # Create a brand-new workspace AFTER the connector exists
    new_ws_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "post-dist-ws", "org_id": org_id},
    )
    assert new_ws_resp.status_code == 201, new_ws_resp.text
    new_ws_id = new_ws_resp.json()["id"]

    # No state row for the new workspace
    async with db_maker() as session:
        states = (
            (
                await session.execute(
                    select(MCPWorkspaceConnectorState).where(
                        MCPWorkspaceConnectorState.connector_id == connector_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        states_by_ws = {s.workspace_id: s for s in states}
        assert new_ws_id not in states_by_ws, (
            "post-distribute workspace must NOT be auto-enrolled when auto_enroll=False"
        )
