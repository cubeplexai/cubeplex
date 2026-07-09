"""E2E coverage for the four-layer MCP routes (Task 6 of the management plan).

Each test maps 1:1 to a scenario in the spec's testing-strategy section:

* ``test_workspace_local_noauth_install_renders_usable`` — spec test #2
* ``test_org_admin_noauth_install_distributed_to_workspace_renders_usable``
  — spec test #1
* ``test_user_grant_policy_does_not_fall_back_to_org_grant`` — spec test #3
* ``test_policy_change_drops_previous_scope_grant_from_runtime`` — spec test #4
* ``test_disconnect_keeps_install_and_state`` — spec test #5
* ``test_uninstall_then_reinstall_same_template`` — spec test #6
* ``test_oauth_refresh_before_runtime_returns_usable`` — spec test #7
  (SKIPPED — four-layer OAuth start returns 501 per Task 4 deviation)
* ``test_invalid_credential_policy_rejected_at_api_boundary`` — spec test #8

The tests intentionally use the **public HTTP surface** (admin + workspace
routes) rather than poking repositories directly, so the contract documented
in ``docs/dev/specs/...`` is exercised exactly as the frontend sees
it. Database-level assertions are added for invariants that aren't visible
via the API alone (install_scope, workspace_id NULL, install_state tombstones,
WorkspaceConnectorState rows).
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


async def _find_connector(
    payload: dict | list,  # type: ignore[type-arg]
    install_id: str,
) -> dict | None:  # type: ignore[type-arg]
    # GET /connectors returns ``{items: [...]}``; tolerate a bare list too so the
    # helper still works if someone reaches for it from a different surface.
    if isinstance(payload, dict):
        rows = payload.get("items", [])
    else:
        rows = payload
    for row in rows:
        if row["install"]["connector_id"] == install_id:
            return row
    return None


# ---------------------------------------------------------------------------
# Scenario #2 — workspace-local no-auth happy path
# ---------------------------------------------------------------------------


async def test_workspace_local_noauth_install_renders_usable(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
) -> None:
    """Workspace admin installs a no-auth template; row is immediately usable."""
    client, workspace_id = admin_client

    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": noauth_template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert resp.status_code == 201, resp.text
    install = resp.json()
    install_id = install["connector_id"]
    assert install["install_scope"] == "workspace"
    assert install["workspace_id"] == workspace_id
    assert install["auth_method"] == "none"
    assert install["install_state"] == "active"

    connectors_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")
    assert connectors_resp.status_code == 200, connectors_resp.text
    row = await _find_connector(connectors_resp.json(), install_id)
    assert row is not None, "expected install in workspace connectors list"
    assert row["install"]["install_scope"] == "workspace"
    assert row["workspace_state"] is not None
    assert row["workspace_state"]["enabled"] is True
    assert row["credential_policy"] == "none"
    assert row["credential_availability"] == "not_required"
    assert row["usable"] is True
    assert row["reason"] == "usable"


# ---------------------------------------------------------------------------
# Scenario #1 — org admin no-auth install + selected-workspace distribution
# ---------------------------------------------------------------------------


async def test_org_admin_noauth_install_distributed_to_workspace_renders_usable(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Org-scope install with ``auto_enable={mode:'selected', ...}`` distributes to
    one workspace; a sibling workspace in the same org does NOT see it.
    """
    client, workspace_id = admin_client

    # Create a second workspace in the same org to verify isolation.
    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200, me_resp.text
    workspaces_resp = await client.get("/api/v1/workspaces")
    assert workspaces_resp.status_code == 200, workspaces_resp.text
    org_id = workspaces_resp.json()[0]["org_id"]

    second_ws_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "sibling-ws", "org_id": org_id},
    )
    assert second_ws_resp.status_code == 201, second_ws_resp.text
    sibling_ws_id = second_ws_resp.json()["id"]

    install_resp = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": noauth_template_id,
            "install_scope": "org",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "selected", "workspace_ids": [workspace_id]},
        },
    )
    assert install_resp.status_code == 201, install_resp.text
    install = install_resp.json()
    install_id = install["connector_id"]
    assert install["install_scope"] == "org"
    assert install["workspace_id"] is None
    assert install["auth_status"] == "not_required"

    # DB-level: install row has workspace_id NULL; state row exists for the
    # targeted workspace with admin_manual enablement; no row for sibling.
    from sqlalchemy import select

    from cubebox.models import MCPConnector, MCPWorkspaceConnectorState

    async with db_maker() as session:
        install_row = await session.get(MCPConnector, install_id)
        assert install_row is not None
        assert install_row.install_scope == "org"
        assert install_row.workspace_id is None

        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.connector_id == install_id  # type: ignore[arg-type]
        )
        states = list((await session.execute(stmt)).scalars().all())
        states_by_ws = {s.workspace_id: s for s in states}
        assert workspace_id in states_by_ws, "expected state row for targeted ws"
        assert states_by_ws[workspace_id].enabled is True
        assert states_by_ws[workspace_id].enablement_source == "admin_manual"
        assert sibling_ws_id not in states_by_ws, "sibling must NOT have state row"

    # API-level: targeted workspace sees usable; sibling does not.
    connectors_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")
    assert connectors_resp.status_code == 200, connectors_resp.text
    row = await _find_connector(connectors_resp.json(), install_id)
    assert row is not None
    assert row["install"]["install_scope"] == "org"
    assert row["credential_availability"] == "not_required"
    assert row["usable"] is True

    sibling_resp = await client.get(f"/api/v1/ws/{sibling_ws_id}/mcp/connectors")
    assert sibling_resp.status_code == 200, sibling_resp.text
    sibling_row = await _find_connector(sibling_resp.json(), install_id)
    assert sibling_row is None, "org install must not auto-leak to non-targeted ws"


# ---------------------------------------------------------------------------
# Scenario #3 — user-policy scope isolation (org grant must NOT satisfy user policy)
# ---------------------------------------------------------------------------


async def test_user_grant_policy_does_not_fall_back_to_org_grant(
    four_layer_admin_and_member: tuple[
        tuple[httpx.AsyncClient, str, str],
        tuple[httpx.AsyncClient, str, str],
    ],
    static_template_id: str,
) -> None:
    """Two-user test: org grant must NOT satisfy a user-policy row.

    The diagnostic reason for missing user-policy must be ``user_needs_connection``,
    not a generic ``credential_missing`` — Task 5's reason matrix protects the UI
    diagnostic surface against accidental collapses.
    """
    (admin_c, workspace_id, _admin_uid), (member_c, _ws_b, _member_uid) = (
        four_layer_admin_and_member
    )

    # 1) Admin installs static template with org policy + org grant
    install_resp = await admin_c.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": static_template_id,
            "install_scope": "org",
            "auth_method": "static",
            "default_credential_policy": "org",
            "auto_enable": {"mode": "selected", "workspace_ids": [workspace_id]},
        },
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["connector_id"]

    org_grant_resp = await admin_c.post(
        f"/api/v1/admin/mcp/installs/{install_id}/grants/org",
        json={"credential_plaintext": "org-shared-token"},
    )
    assert org_grant_resp.status_code == 201, org_grant_resp.text

    # Sanity: at org-policy + org-grant, the connector is usable for both users.
    rows = (await admin_c.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")).json()
    pre_flip = await _find_connector(rows, install_id)
    assert pre_flip is not None and pre_flip["usable"] is True

    # 2) Workspace admin flips policy to ``user`` on the workspace state row.
    flip_resp = await admin_c.patch(
        f"/api/v1/ws/{workspace_id}/mcp/connectors/{install_id}/state",
        json={"credential_policy": "user"},
    )
    assert flip_resp.status_code == 200, flip_resp.text
    assert flip_resp.json()["credential_policy"] == "user"

    # Without a user grant, BOTH users see missing + scope-specific reason.
    admin_view = await admin_c.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")
    member_view = await member_c.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")
    assert admin_view.status_code == 200, admin_view.text
    assert member_view.status_code == 200, member_view.text

    admin_row = await _find_connector(admin_view.json(), install_id)
    member_row = await _find_connector(member_view.json(), install_id)
    assert admin_row is not None
    assert member_row is not None

    for label, row in (("admin", admin_row), ("member", member_row)):
        assert row["credential_policy"] == "user", label
        assert row["credential_availability"] == "missing", label
        assert row["usable"] is False, label
        # Scope-specific reason — must NOT collapse to a generic
        # ``credential_missing``. The org grant created above must not satisfy
        # a user-policy install.
        assert row["reason"] == "user_needs_connection", (label, row["reason"])

    # 3) Admin user (user A) connects their own user-scope grant. Admin → usable.
    #    Member (user B) still sees user_needs_connection.
    user_grant_resp = await admin_c.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/me",
        json={"credential_plaintext": "userA-token"},
    )
    assert user_grant_resp.status_code == 201, user_grant_resp.text

    admin_view2 = (await admin_c.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")).json()
    member_view2 = (await member_c.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")).json()

    admin_row2 = await _find_connector(admin_view2, install_id)
    member_row2 = await _find_connector(member_view2, install_id)
    assert admin_row2 is not None and member_row2 is not None
    assert admin_row2["usable"] is True
    assert admin_row2["reason"] == "usable"
    assert admin_row2["credential_availability"] == "available"
    assert admin_row2["credential_source"] == "user"

    assert member_row2["usable"] is False
    assert member_row2["reason"] == "user_needs_connection"
    assert member_row2["credential_availability"] == "missing"


# ---------------------------------------------------------------------------
# Scenario #4 — policy change drops previous-scope grant from runtime
# ---------------------------------------------------------------------------


async def test_policy_change_drops_previous_scope_grant_from_runtime(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
) -> None:
    """Flip credential_policy from org → workspace; the org grant no longer
    satisfies the install. Without a workspace grant, ``usable=false`` +
    ``reason='missing_workspace_grant'``.
    """
    client, workspace_id = admin_client

    install_resp = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": static_template_id,
            "install_scope": "org",
            "auth_method": "static",
            "default_credential_policy": "org",
            "auto_enable": {"mode": "selected", "workspace_ids": [workspace_id]},
        },
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["connector_id"]

    grant_resp = await client.post(
        f"/api/v1/admin/mcp/installs/{install_id}/grants/org",
        json={"credential_plaintext": "org-token"},
    )
    assert grant_resp.status_code == 201, grant_resp.text

    rows = (await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")).json()
    row_before = await _find_connector(rows, install_id)
    assert row_before is not None and row_before["usable"] is True
    assert row_before["credential_source"] == "org"

    # Flip the workspace policy to "workspace". The org grant must no longer
    # back this install.
    flip = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/connectors/{install_id}/state",
        json={"credential_policy": "workspace"},
    )
    assert flip.status_code == 200, flip.text

    rows2 = (await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")).json()
    row_after = await _find_connector(rows2, install_id)
    assert row_after is not None
    assert row_after["credential_policy"] == "workspace"
    assert row_after["credential_source"] != "org"
    assert row_after["usable"] is False
    assert row_after["reason"] == "missing_workspace_grant"


# ---------------------------------------------------------------------------
# Scenario #5 — disconnect keeps install + state rows
# ---------------------------------------------------------------------------


async def test_disconnect_keeps_install_and_state(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Deleting an org grant leaves install + workspace_state intact and
    flips ``usable`` to False; re-creating the grant restores usability without
    a re-install.
    """
    client, workspace_id = admin_client

    install_resp = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": static_template_id,
            "install_scope": "org",
            "auth_method": "static",
            "default_credential_policy": "org",
            "auto_enable": {"mode": "selected", "workspace_ids": [workspace_id]},
        },
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["connector_id"]

    grant_resp = await client.post(
        f"/api/v1/admin/mcp/installs/{install_id}/grants/org",
        json={"credential_plaintext": "org-token"},
    )
    assert grant_resp.status_code == 201, grant_resp.text

    pre_rows = (await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")).json()
    pre = await _find_connector(pre_rows, install_id)
    assert pre is not None and pre["usable"] is True

    # Disconnect (delete the org grant).
    disconnect = await client.delete(f"/api/v1/admin/mcp/installs/{install_id}/grants/org")
    assert disconnect.status_code == 204, disconnect.text

    # DB invariants: install + state rows survive disconnect.
    from sqlalchemy import select

    from cubebox.models import MCPConnector, MCPWorkspaceConnectorState

    async with db_maker() as session:
        install_row = await session.get(MCPConnector, install_id)
        assert install_row is not None
        assert install_row.install_state == "active"

        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.connector_id == install_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
        state = (await session.execute(stmt)).scalar_one_or_none()
        assert state is not None and state.enabled is True

    # Effective state: unusable + scope-specific reason ``missing_org_grant``.
    mid_rows = (await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")).json()
    mid = await _find_connector(mid_rows, install_id)
    assert mid is not None
    assert mid["usable"] is False
    assert mid["reason"] == "missing_org_grant"

    # Re-grant → immediately usable again (no reinstall).
    # NOTE: disconnect_grant only removes the grant row, not the credential
    # vault row it points at, so a fresh org grant on the same install must
    # pass an explicit ``name`` to avoid colliding with the previous default
    # credential name (``mcp:{install_id}:org``).
    regrant = await client.post(
        f"/api/v1/admin/mcp/installs/{install_id}/grants/org",
        json={"credential_plaintext": "org-token-v2", "name": "regrant-v2"},
    )
    assert regrant.status_code == 201, regrant.text

    after_rows = (await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")).json()
    after = await _find_connector(after_rows, install_id)
    assert after is not None and after["usable"] is True
    assert after["reason"] == "usable"


# ---------------------------------------------------------------------------
# Regression: re-POST an org grant must upsert (not collide on the partial
# unique index). If a previous attempt left a row behind — e.g. discovery
# raised an unexpected error after grant_repo.add committed — the next
# attempt should replace the row, not 500 on uq_mcp_credential_grant_org.
# ---------------------------------------------------------------------------


async def test_repost_org_grant_is_idempotent(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
) -> None:
    client, workspace_id = admin_client

    install_resp = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": static_template_id,
            "install_scope": "org",
            "auth_method": "static",
            "default_credential_policy": "org",
            "auto_enable": {"mode": "selected", "workspace_ids": [workspace_id]},
        },
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["connector_id"]

    first = await client.post(
        f"/api/v1/admin/mcp/installs/{install_id}/grants/org",
        json={"credential_plaintext": "org-token-v1"},
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        f"/api/v1/admin/mcp/installs/{install_id}/grants/org",
        json={"credential_plaintext": "org-token-v2"},
    )
    assert second.status_code == 201, second.text


# ---------------------------------------------------------------------------
# Scenario #6 — uninstall then reinstall same template
# ---------------------------------------------------------------------------


async def test_uninstall_then_reinstall_same_template(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """DELETE an install → ``install_state='uninstalled'`` + invisible in GET;
    POST a fresh install of the same template succeeds (partial unique index
    ignores tombstones).
    """
    client, workspace_id = admin_client

    first_resp = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": noauth_template_id,
            "install_scope": "org",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "selected", "workspace_ids": [workspace_id]},
        },
    )
    assert first_resp.status_code == 201, first_resp.text
    first_install_id = first_resp.json()["connector_id"]

    # Uninstall the first install.
    delete_resp = await client.delete(f"/api/v1/admin/mcp/installs/{first_install_id}")
    assert delete_resp.status_code == 204, delete_resp.text

    from cubebox.models import MCPConnector

    async with db_maker() as session:
        row = await session.get(MCPConnector, first_install_id)
        assert row is not None
        assert row.install_state == "uninstalled"

    # No longer visible to the workspace view.
    rows = (await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")).json()
    assert await _find_connector(rows, first_install_id) is None

    # Reinstall the same template — partial unique index excludes the
    # tombstoned row so this must succeed.
    second_resp = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": noauth_template_id,
            "install_scope": "org",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "selected", "workspace_ids": [workspace_id]},
        },
    )
    assert second_resp.status_code == 201, second_resp.text
    second_install_id = second_resp.json()["connector_id"]
    assert second_install_id != first_install_id


# ---------------------------------------------------------------------------
# Scenario #7 — OAuth refresh before runtime returns usable (SKIPPED)
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="oauth_start_not_yet_wired_for_four_layer_grants")
async def test_oauth_refresh_before_runtime_returns_usable(
    admin_client: tuple[httpx.AsyncClient, str],
    oauth_template_id: str,
) -> None:
    """TODO: re-enable once four-layer OAuth start is wired.

    Task 4 of the MCP management plan landed the four-layer OAuth start route as
    a stub returning 501 (``mcp_oauth.four_layer_start_not_yet_wired``). Without
    a real OAuth flow we cannot insert a refresh-capable grant via the public
    surface; bypassing the API to seed the grant + refresh credential directly
    would test internals instead of the spec. The follow-up OAuth task takes
    the skip off this test.

    When unskipped, the test should:

    1. Install an OAuth template with credential_policy="user" + a stubbed
       refresh credential whose ``expires_at`` is in the past.
    2. Stub :class:`OAuthTokenManager`'s HTTP refresh call (the existing OAuth
       fixture in ``tests/e2e/mcp_oauth/conftest.py`` is the right helper).
    3. Call the runtime spec endpoint / loader so the manager rotates the
       access token.
    4. Assert: refresh was hit exactly once; the install reports
       ``grant_status='valid'``; the connector is usable with the freshly-
       rotated ``credential_id`` stored on the grant row.
    """
    _client, _workspace_id = admin_client
    _ = oauth_template_id


# ---------------------------------------------------------------------------
# Scenario #8 — invalid credential policy rejected at API boundary
# ---------------------------------------------------------------------------


async def test_invalid_credential_policy_rejected_at_api_boundary(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
) -> None:
    """422 + field error on ``credential_policy`` for both:

    1. ``credential_policy='bogus'`` (Literal-rejected by pydantic).
    2. ``credential_policy='none'`` with ``auth_method='static'`` (cross-field
       validator). The same combo is unit-covered by the handler test in Task 4;
       this E2E variant confirms the response shape is visible end-to-end.
    """
    client, _workspace_id = admin_client

    bogus_resp = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": static_template_id,
            "install_scope": "org",
            "auth_method": "static",
            "default_credential_policy": "bogus",
            "auto_enable": {"mode": "none"},
        },
    )
    assert bogus_resp.status_code == 422, bogus_resp.text
    bogus_detail = bogus_resp.json()["detail"]
    assert any("default_credential_policy" in (err.get("loc") or []) for err in bogus_detail), (
        bogus_detail
    )

    inconsistent_resp = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": static_template_id,
            "install_scope": "org",
            "auth_method": "static",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert inconsistent_resp.status_code == 422, inconsistent_resp.text


# ---------------------------------------------------------------------------
# Scenario #9 — PATCH connector state upserts for org installs in same org
# ---------------------------------------------------------------------------


async def test_patch_state_upserts_for_org_install_with_no_state_row(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """An org install created with ``auto_enable.mode='none'`` has no state row
    in any workspace by design. The admin Workspaces tab UI renders such an
    install as "disabled" and lets the admin toggle a checkbox to enable it.
    That checkbox calls PATCH /ws/{ws}/mcp/connectors/{install_id}/state, so
    that handler must UPSERT — not 404 — for org-scope installs in the same
    org. The second PATCH must update the row, not duplicate it.
    """
    from sqlalchemy import select

    from cubebox.models import MCPWorkspaceConnectorState

    client, workspace_id = admin_client

    install_resp = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": static_template_id,
            "install_scope": "org",
            "auth_method": "static",
            "default_credential_policy": "org",
            "auto_enable": {"mode": "none"},
        },
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["connector_id"]

    # No state rows exist anywhere for this install yet.
    async with db_maker() as session:
        rows = (
            (
                await session.execute(
                    select(MCPWorkspaceConnectorState).where(
                        MCPWorkspaceConnectorState.connector_id == install_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == [], f"expected no state rows pre-PATCH, got {rows}"

    # First PATCH: enable + set credential_policy. Must create the row.
    first = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/connectors/{install_id}/state",
        json={"enabled": True, "credential_policy": "org"},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["enabled"] is True
    assert body["credential_policy"] == "org"
    assert body["enablement_source"] == "workspace_manual"

    async with db_maker() as session:
        rows = (
            (
                await session.execute(
                    select(MCPWorkspaceConnectorState).where(
                        MCPWorkspaceConnectorState.connector_id == install_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, f"expected exactly one state row, got {rows}"
        assert rows[0].workspace_id == workspace_id
        assert rows[0].enabled is True
        assert rows[0].credential_policy == "org"
        assert rows[0].enablement_source == "workspace_manual"

    # Second PATCH in the same workspace: must UPDATE the existing row, not
    # insert a duplicate (the unique constraint
    # uq_mcp_workspace_connector_state would 500 if we double-inserted).
    second = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/connectors/{install_id}/state",
        json={"enabled": False},
    )
    assert second.status_code == 200, second.text
    body2 = second.json()
    assert body2["enabled"] is False
    # Policy unchanged from the previous row, not reset to the install default.
    assert body2["credential_policy"] == "org"

    async with db_maker() as session:
        rows = (
            (
                await session.execute(
                    select(MCPWorkspaceConnectorState).where(
                        MCPWorkspaceConnectorState.connector_id == install_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, f"second PATCH must update, not insert; got {rows}"
        assert rows[0].enabled is False
        assert rows[0].credential_policy == "org"


# ---------------------------------------------------------------------------
# Scenario #10 — selected-distribution install must NOT auto-enroll new ws
# ---------------------------------------------------------------------------


async def test_selected_distribution_does_not_auto_enroll_new_workspace(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """An org install with ``auto_enable.mode='selected'`` must NOT fan out
    to workspaces created later.

    The bootstrap hook ``enroll_workspace_in_org_wide_mcp`` fires on every
    workspace create (see ``POST /api/v1/workspaces``) and enrolls any
    active org-scope install whose ``auto_enroll_new_workspaces`` is True.
    If the create-time service leaves that flag at the model's
    ``server_default=true`` for ``selected``/``none`` distributions, then
    a later workspace would silently inherit the install — directly
    contradicting the admin's explicit scoping at install time.
    """
    from sqlalchemy import select

    from cubebox.models import MCPConnector, MCPWorkspaceConnectorState

    client, workspace_id = admin_client

    workspaces_resp = await client.get("/api/v1/workspaces")
    assert workspaces_resp.status_code == 200, workspaces_resp.text
    org_id = workspaces_resp.json()[0]["org_id"]

    install_resp = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": noauth_template_id,
            "install_scope": "org",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "selected", "workspace_ids": [workspace_id]},
        },
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["connector_id"]

    # Persisted flag is explicitly False, NOT the model server_default of True.
    async with db_maker() as session:
        install_row = await session.get(MCPConnector, install_id)
        assert install_row is not None
        assert install_row.auto_enroll_new_workspaces is False, (
            "selected distribution must persist auto_enroll_new_workspaces=False"
        )

    # Create a brand-new workspace in the same org AFTER the install exists.
    # The workspace-create handler invokes ``enroll_workspace_in_org_wide_mcp``
    # which is where a wrongly-defaulted flag would leak the install.
    new_ws_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "post-install-ws", "org_id": org_id},
    )
    assert new_ws_resp.status_code == 201, new_ws_resp.text
    new_ws_id = new_ws_resp.json()["id"]

    # No state row for the new workspace — only the originally-targeted one.
    async with db_maker() as session:
        states = (
            (
                await session.execute(
                    select(MCPWorkspaceConnectorState).where(
                        MCPWorkspaceConnectorState.connector_id == install_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        states_by_ws = {s.workspace_id: s for s in states}
        assert workspace_id in states_by_ws, (
            "originally-targeted workspace should still have its state row"
        )
        assert new_ws_id not in states_by_ws, (
            "post-install workspace must NOT be auto-enrolled in a 'selected' distribution install"
        )
