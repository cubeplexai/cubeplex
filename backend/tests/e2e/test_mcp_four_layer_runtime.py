"""E2E runtime coverage for template-centric MCP connectors.

Complements ``test_mcp_four_layer_routes.py``: the HTTP routes already verify
the install + state + grant lifecycle; this module exercises the runtime
loader's view of an effective connector list. We invoke
:meth:`MCPEffectiveConnectorService.list_runtime_specs` directly so the test
doesn't have to bring up a full SSE agent run.

Old tests used POST /admin/mcp/installs and POST /ws/{ws}/mcp/installs which
were removed in Task 9. Rewritten to use the template-create + distribute
surface instead. The ws_mcp-dependent tests (workspace explicit enable/disable
affecting runtime visibility) are deferred to Task 10.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url
from cubebox.mcp.effective import (
    MCPEffectiveConnectorService,
    MCPRuntimeConnectorSpec,
)
from cubebox.models import Workspace
from cubebox.repositories.mcp import (
    MCPConnectorRepository,
    MCPConnectorTemplateRepository,
    MCPCredentialGrantRepository,
    MCPTemplateSettingsRepository,
    MCPWorkspaceConnectorStateRepository,
)

pytestmark = pytest.mark.usefixtures("stub_discover_tools")


@pytest_asyncio.fixture
async def db_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    eng = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    finally:
        await eng.dispose()


async def _build_effective_service(
    *,
    session: AsyncSession,
    org_id: str,
) -> MCPEffectiveConnectorService:
    return MCPEffectiveConnectorService(
        template_repo=MCPConnectorTemplateRepository(session),
        settings_repo=MCPTemplateSettingsRepository(session, org_id=org_id),
        install_repo=MCPConnectorRepository(session, org_id=org_id),
        state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=org_id),
        grant_repo=MCPCredentialGrantRepository(session, org_id=org_id),
        org_id=org_id,
    )


async def test_noauth_runtime_spec_returns_install_without_grant_lookup(
    admin_client: tuple[httpx.AsyncClient, str],
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Smoke: a no-auth connector distributed to a workspace shows up as a
    runtime spec; no grant lookup hits the DB because
    ``compute_effective_state`` short-circuits on ``credential_policy='none'``.

    We assert no grant lookup by spying on the grant repo's
    :meth:`get_for_connector_scope`; the effective service returns
    ``usable=true`` with ``credential_availability='not_required'`` before
    any of those are called.
    """
    client, workspace_id = admin_client
    suffix = secrets.token_hex(4)

    # Create template and distribute to all workspaces (enable_existing=True).
    tpl_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"Runtime Spec No-Auth {suffix}",
            "server_url": f"https://runtime-noauth-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert tpl_resp.status_code == 201, tpl_resp.text
    template_id = tpl_resp.json()["template_id"]

    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200, me_resp.text
    user_id = me_resp.json()["id"]

    async with db_maker() as session:
        ws = await session.get(Workspace, workspace_id)
        assert ws is not None
        org_id = ws.org_id

        svc = await _build_effective_service(session=session, org_id=org_id)
        # Spy on the grant repo: a no-auth install must not hit any grant
        # lookup path (the effective service's ``_resolve_grant`` returns None
        # immediately for ``policy='none'``).
        grant_spy = AsyncMock(wraps=svc._grant_repo.get_for_connector_scope)
        svc._grant_repo.get_for_connector_scope = grant_spy  # type: ignore[method-assign]

        specs = await svc.list_runtime_specs(workspace_id, user_id)

    matching = [s for s in specs if s.connector_id == connector_id]
    assert len(matching) == 1, f"expected exactly one runtime spec, got {specs!r}"
    spec = matching[0]
    assert isinstance(spec, MCPRuntimeConnectorSpec)
    assert spec.auth_method == "none"
    assert spec.grant_scope is None
    assert spec.credential_id is None
    assert spec.refresh_credential_id is None

    grant_spy.assert_not_awaited()


async def test_org_connector_only_visible_in_workspace_with_state_row(
    admin_client: tuple[httpx.AsyncClient, str],
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """A connector distributed with ``enable_existing=False`` creates no state
    rows. It is therefore invisible to ALL workspace runtime specs until a
    state row is manually added.

    This replaces the old ``auto_enable={'mode':'selected', workspace_ids:[A]}``
    test: the invariant is the same (no state row → not in runtime spec list),
    but the setup is simpler — distribute skips existing workspaces rather than
    cherry-picking.
    """
    client, workspace_id = admin_client
    suffix = secrets.token_hex(4)

    workspaces_resp = await client.get("/api/v1/workspaces")
    assert workspaces_resp.status_code == 200, workspaces_resp.text
    org_id = workspaces_resp.json()[0]["org_id"]

    # Create a second workspace to act as "sibling".
    second_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": f"runtime-sibling-{suffix}", "org_id": org_id},
    )
    assert second_resp.status_code == 201, second_resp.text
    sibling_id = second_resp.json()["id"]

    # Distribute with enable_existing=False → no state rows at all.
    tpl_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"Scoped Visible {suffix}",
            "server_url": f"https://scoped-visible-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert tpl_resp.status_code == 201, tpl_resp.text
    template_id = tpl_resp.json()["template_id"]

    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": False, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200
    user_id = me_resp.json()["id"]

    # Manually add a state row for workspace_id (the primary workspace) only.
    async with db_maker() as session:
        state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
        await state_repo.upsert_for_connector(
            workspace_id=workspace_id,
            connector_id=connector_id,
            enabled=True,
            credential_policy="none",
            enablement_source="admin_manual",
            updated_by_user_id=user_id,
        )
        await session.commit()

    async with db_maker() as session:
        svc = await _build_effective_service(session=session, org_id=org_id)

        targeted_specs = await svc.list_runtime_specs(workspace_id, user_id)
        sibling_specs = await svc.list_runtime_specs(sibling_id, user_id)

    assert any(s.connector_id == connector_id for s in targeted_specs), (
        "targeted workspace should see connector in its runtime spec list"
    )
    assert not any(s.connector_id == connector_id for s in sibling_specs), (
        "sibling workspace must not see connector absent a state row"
    )
