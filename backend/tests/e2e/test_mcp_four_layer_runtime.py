"""E2E runtime coverage for four-layer MCP installs.

Complements ``test_mcp_four_layer_routes.py``: the HTTP routes already verify
the install + state + grant lifecycle; this module exercises the runtime
loader's view of an effective connector list. We invoke
:meth:`MCPEffectiveConnectorService.list_runtime_specs` directly so the test
doesn't have to bring up a full SSE agent run.

OAuth-refresh coverage (spec test #7) lives in
``test_mcp_four_layer_routes.py::test_oauth_refresh_before_runtime_returns_usable``
and is currently skipped — four-layer OAuth start is stubbed (501) until the
OAuth follow-up task lands.
"""

from __future__ import annotations

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
        install_repo=MCPConnectorRepository(session, org_id=org_id),
        state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=org_id),
        grant_repo=MCPCredentialGrantRepository(session, org_id=org_id),
        org_id=org_id,
    )


async def test_noauth_runtime_spec_returns_install_without_grant_lookup(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Smoke: a no-auth install shows up as a runtime spec; no grant lookup hits
    the DB because ``compute_effective_state`` short-circuits on
    ``auth_method='none'``.

    We assert no grant lookup by spying on the grant repo's
    :meth:`get_org_grant` / :meth:`get_workspace_grant` / :meth:`get_user_grant`
    methods; rule #5 of ``compute_effective_state`` returns ``usable=true`` with
    ``credential_availability='not_required'`` before any of those are called.
    """
    client, workspace_id = admin_client

    install_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": noauth_template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["connector_id"]

    async with db_maker() as session:
        ws = await session.get(Workspace, workspace_id)
        assert ws is not None
        org_id = ws.org_id

        # Look up the admin's user_id via the auth ``/me`` endpoint instead of
        # poking the membership table; the effective service only uses it for
        # user-scope grant lookup which doesn't apply here.
        me_resp = await client.get("/api/v1/auth/me")
        assert me_resp.status_code == 200, me_resp.text
        user_id = me_resp.json()["id"]

        svc = await _build_effective_service(session=session, org_id=org_id)
        # Spy on the grant repo: a no-auth install must not hit any grant
        # lookup path (the effective service's ``_resolve_grant`` returns None
        # immediately for ``policy='none'``).
        grant_spy = AsyncMock(wraps=svc._grant_repo.get_for_connector_scope)
        svc._grant_repo.get_for_connector_scope = grant_spy  # type: ignore[method-assign]

        specs = await svc.list_runtime_specs(workspace_id, user_id)

    matching = [s for s in specs if s.connector_id == install_id]
    assert len(matching) == 1, f"expected exactly one runtime spec, got {specs!r}"
    spec = matching[0]
    assert isinstance(spec, MCPRuntimeConnectorSpec)
    assert spec.auth_method == "none"
    assert spec.grant_scope is None
    assert spec.credential_id is None
    assert spec.refresh_credential_id is None

    grant_spy.assert_not_awaited()


async def test_noauth_org_install_runtime_only_visible_in_targeted_workspace(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Org install with ``auto_enable={mode:'selected', workspace_ids:[A]}`` is
    visible to workspace A's runtime spec list but not to a sibling workspace B
    in the same org.

    Complements scenario #1 (HTTP surface) with a runtime-layer assertion: the
    cubepi loader consumes runtime specs, so spec-list isolation is the contract
    that matters in production.
    """
    client, workspace_id = admin_client

    workspaces_resp = await client.get("/api/v1/workspaces")
    assert workspaces_resp.status_code == 200, workspaces_resp.text
    org_id = workspaces_resp.json()[0]["org_id"]

    second_resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "runtime-sibling-ws", "org_id": org_id},
    )
    assert second_resp.status_code == 201, second_resp.text
    sibling_id = second_resp.json()["id"]

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

    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200
    user_id = me_resp.json()["id"]

    async with db_maker() as session:
        svc = await _build_effective_service(session=session, org_id=org_id)

        targeted_specs = await svc.list_runtime_specs(workspace_id, user_id)
        sibling_specs = await svc.list_runtime_specs(sibling_id, user_id)

    assert any(s.connector_id == install_id for s in targeted_specs), (
        "targeted workspace should see install in its runtime spec list"
    )
    assert not any(s.connector_id == install_id for s in sibling_specs), (
        "sibling workspace must not see org install absent a state row"
    )
