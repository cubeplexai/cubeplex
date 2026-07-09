"""Tests for :class:`cubebox.mcp.effective.MCPEffectiveConnectorService`.

The service is the join point between connector identity rows, workspace
state, and credential grants. These tests exercise the workspace-visibility
rules: connectors surface only where a workspace state row exists; inactive
connectors are tombstones.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.mcp.effective import MCPEffectiveConnectorService
from cubebox.models import MCPConnector, MCPCredentialGrant, MCPWorkspaceConnectorState
from cubebox.repositories.mcp import (
    MCPConnectorRepository,
    MCPConnectorTemplateRepository,
    MCPCredentialGrantRepository,
    MCPWorkspaceConnectorStateRepository,
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db_session:
        yield db_session
    await engine.dispose()


def _make_service(session: AsyncSession, *, org_id: str) -> MCPEffectiveConnectorService:
    return MCPEffectiveConnectorService(
        template_repo=MCPConnectorTemplateRepository(session),
        connector_repo=MCPConnectorRepository(session, org_id=org_id),
        state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=org_id),
        grant_repo=MCPCredentialGrantRepository(session, org_id=org_id),
        org_id=org_id,
    )


async def _add_workspace_connector(
    session: AsyncSession,
    *,
    connector_id: str,
    org_id: str,
    workspace_id: str,
    status: str = "active",
    auth_method: str = "none",
    credential_policy: str = "none",
    auth_status: str = "not_required",
) -> MCPConnector:
    connector = MCPConnector(
        id=connector_id,
        org_id=org_id,
        template_id=None,
        name=f"ws-connector-{workspace_id}-{connector_id}",
        server_url=f"https://mcp.example/{connector_id}",
        server_url_hash=connector_id,
        transport="streamable_http",
        auth_method=auth_method,
        default_credential_policy=credential_policy,
        auth_status=auth_status,
        status=status,
        created_by_user_id="u1",
    )
    session.add(connector)
    await session.commit()
    await session.refresh(connector)
    return connector


async def _add_org_connector(
    session: AsyncSession,
    *,
    connector_id: str,
    org_id: str,
    status: str = "active",
) -> MCPConnector:
    connector = MCPConnector(
        id=connector_id,
        org_id=org_id,
        template_id=None,
        name=f"org-connector-{connector_id}",
        server_url=f"https://mcp.example/org/{connector_id}",
        server_url_hash=connector_id,
        transport="streamable_http",
        auth_method="none",
        default_credential_policy="none",
        auth_status="not_required",
        status=status,
        created_by_user_id="u1",
    )
    session.add(connector)
    await session.commit()
    await session.refresh(connector)
    return connector


async def test_connector_only_visible_to_enabled_workspace(
    session: AsyncSession,
) -> None:
    """A connector enabled in ws-A is invisible to ws-B in the same org."""
    org_id = "org-1"
    await _add_workspace_connector(
        session,
        connector_id="mcpco-ws-a-1",
        org_id=org_id,
        workspace_id="ws-a",
    )

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    await state_repo.upsert_for_connector(
        workspace_id="ws-a",
        connector_id="mcpco-ws-a-1",
        enabled=True,
        credential_policy="none",
        enablement_source="workspace_manual",
        updated_by_user_id="u1",
    )

    service = _make_service(session, org_id=org_id)

    rows_a = await service.list_for_workspace_user("ws-a", "u1")
    assert [row.connector.id for row in rows_a] == ["mcpco-ws-a-1"]
    assert rows_a[0].usable is True

    rows_b = await service.list_for_workspace_user("ws-b", "u1")
    assert rows_b == []


async def test_org_connector_requires_workspace_state_to_surface(
    session: AsyncSession,
) -> None:
    """Connector + state row in ws-A is visible to ws-A but not ws-B."""
    org_id = "org-1"
    await _add_org_connector(session, connector_id="mcpco-org-1", org_id=org_id)

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    await state_repo.upsert_for_connector(
        workspace_id="ws-a",
        connector_id="mcpco-org-1",
        enabled=True,
        credential_policy="none",
        enablement_source="admin_manual",
        updated_by_user_id="u1",
    )

    service = _make_service(session, org_id=org_id)

    rows_a = await service.list_for_workspace_user("ws-a", "u1")
    assert [row.connector.id for row in rows_a] == ["mcpco-org-1"]
    assert rows_a[0].usable is True

    rows_b = await service.list_for_workspace_user("ws-b", "u1")
    assert rows_b == []


async def test_uninstalled_rows_are_filtered(session: AsyncSession) -> None:
    """``status='uninstalled'`` rows are tombstones; not surfaced at all."""
    org_id = "org-1"
    await _add_workspace_connector(
        session,
        connector_id="mcpco-active",
        org_id=org_id,
        workspace_id="ws-a",
    )
    await _add_workspace_connector(
        session,
        connector_id="mcpco-tombstone",
        org_id=org_id,
        workspace_id="ws-a",
        status="uninstalled",
    )

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    await state_repo.upsert_for_connector(
        workspace_id="ws-a",
        connector_id="mcpco-active",
        enabled=True,
        credential_policy="none",
        enablement_source="workspace_manual",
        updated_by_user_id="u1",
    )
    await state_repo.upsert_for_connector(
        workspace_id="ws-a",
        connector_id="mcpco-tombstone",
        enabled=True,
        credential_policy="none",
        enablement_source="workspace_manual",
        updated_by_user_id="u1",
    )

    service = _make_service(session, org_id=org_id)
    rows = await service.list_for_workspace_user("ws-a", "u1")
    assert [row.connector.id for row in rows] == ["mcpco-active"]


async def test_list_for_workspace_user_excludes_disabled_org_installs(
    session: AsyncSession,
) -> None:
    """include_disabled_org_installs=False drops connectors with disabled or absent state."""
    org_id = "org-1"
    workspace_id = "ws-1"

    await _add_workspace_connector(
        session,
        connector_id="mcpco-ws-local",
        org_id=org_id,
        workspace_id=workspace_id,
    )
    await _add_org_connector(session, connector_id="mcpco-org-enabled", org_id=org_id)
    await _add_org_connector(session, connector_id="mcpco-org-disabled", org_id=org_id)
    await _add_org_connector(session, connector_id="mcpco-org-no-state", org_id=org_id)

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    await state_repo.upsert_for_connector(
        workspace_id=workspace_id,
        connector_id="mcpco-ws-local",
        enabled=True,
        credential_policy="none",
        enablement_source="workspace_manual",
        updated_by_user_id="u1",
    )
    await state_repo.upsert_for_connector(
        workspace_id=workspace_id,
        connector_id="mcpco-org-enabled",
        enabled=True,
        credential_policy="none",
        enablement_source="admin_manual",
        updated_by_user_id="u1",
    )
    await state_repo.upsert_for_connector(
        workspace_id=workspace_id,
        connector_id="mcpco-org-disabled",
        enabled=False,
        credential_policy="none",
        enablement_source="admin_manual",
        updated_by_user_id="u1",
    )
    # mcpco-org-no-state intentionally has no state row.

    service = _make_service(session, org_id=org_id)
    out = await service.list_for_workspace_user(
        workspace_id,
        "u1",
        include_unusable=True,
        include_disabled_org_installs=False,
    )
    ids = {row.connector.id for row in out}
    assert "mcpco-ws-local" in ids
    assert "mcpco-org-enabled" in ids
    assert "mcpco-org-disabled" not in ids
    assert "mcpco-org-no-state" not in ids


async def test_list_for_workspace_user_default_keeps_disabled_org_installs(
    session: AsyncSession,
) -> None:
    """Backwards compat: default (True) keeps disabled-org rows visible."""
    org_id = "org-1"
    workspace_id = "ws-1"

    await _add_org_connector(session, connector_id="mcpco-org-disabled", org_id=org_id)

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    await state_repo.upsert_for_connector(
        workspace_id=workspace_id,
        connector_id="mcpco-org-disabled",
        enabled=False,
        credential_policy="none",
        enablement_source="admin_manual",
        updated_by_user_id="u1",
    )

    service = _make_service(session, org_id=org_id)
    out = await service.list_for_workspace_user(
        workspace_id,
        "u1",
        include_unusable=True,
        # default include_disabled_org_installs=True
    )
    ids = {row.connector.id for row in out}
    assert "mcpco-org-disabled" in ids


async def test_list_runtime_specs_drops_unusable_rows(session: AsyncSession) -> None:
    """``list_runtime_specs`` returns only usable connectors."""
    org_id = "org-1"
    # Usable: connector enabled in the workspace, no auth.
    await _add_workspace_connector(
        session,
        connector_id="mcpco-good",
        org_id=org_id,
        workspace_id="ws-a",
    )
    # Unusable: connector with state row missing.
    await _add_workspace_connector(
        session,
        connector_id="mcpco-no-state",
        org_id=org_id,
        workspace_id="ws-a",
    )
    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    await state_repo.upsert_for_connector(
        workspace_id="ws-a",
        connector_id="mcpco-good",
        enabled=True,
        credential_policy="none",
        enablement_source="workspace_manual",
        updated_by_user_id="u1",
    )

    service = _make_service(session, org_id=org_id)
    specs = await service.list_runtime_specs("ws-a", "u1")
    assert [spec.connector_id for spec in specs] == ["mcpco-good"]
    assert specs[0].name == "ws-connector-ws-a-mcpco-good"
    assert specs[0].auth_method == "none"
    assert specs[0].transport == "streamable_http"


async def test_list_for_workspace_user_reports_saved_grants_for_each_scope(
    session: AsyncSession,
) -> None:
    """Policy badges need availability for every selectable credential scope."""
    org_id = "org-1"
    workspace_id = "ws-a"
    user_id = "u1"
    connector = await _add_workspace_connector(
        session,
        connector_id="mcpco-static",
        org_id=org_id,
        workspace_id=workspace_id,
        auth_method="static",
        credential_policy="workspace",
        auth_status="connected",
    )
    state = MCPWorkspaceConnectorState(
        org_id=org_id,
        workspace_id=workspace_id,
        connector_id=connector.id,
        enabled=True,
        credential_policy="workspace",
        enablement_source="workspace_manual",
        updated_by_user_id=user_id,
    )
    grants = {
        "org": MCPCredentialGrant(
            org_id=org_id,
            connector_id=connector.id,
            grant_scope="org",
            credential_id="cred-org",
            created_by_user_id=user_id,
        ),
        "workspace": MCPCredentialGrant(
            org_id=org_id,
            connector_id=connector.id,
            grant_scope="workspace",
            workspace_id=workspace_id,
            credential_id="cred-workspace",
            created_by_user_id=user_id,
        ),
        "user": MCPCredentialGrant(
            org_id=org_id,
            connector_id=connector.id,
            grant_scope="user",
            workspace_id=workspace_id,
            user_id=user_id,
            credential_id="cred-user",
            created_by_user_id=user_id,
        ),
    }

    class ConnectorRepo:
        async def list_active(self) -> list[MCPConnector]:
            return [connector]

    class StateRepo:
        async def list_for_workspace(
            self,
            requested_workspace_id: str,
        ) -> list[MCPWorkspaceConnectorState]:
            assert requested_workspace_id == workspace_id
            return [state]

    class TemplateRepo:
        async def get(self, _template_id: str) -> None:
            return None

    class GrantRepo:
        async def get_for_connector_scope(
            self,
            *,
            connector_id: str,
            grant_scope: str,
            workspace_id: str | None,
            user_id: str | None,
        ) -> MCPCredentialGrant | None:
            assert connector_id == connector.id
            if grant_scope == "workspace":
                assert workspace_id == "ws-a"
            if grant_scope == "user":
                assert workspace_id == "ws-a"
                assert user_id == "u1"
            return grants.get(grant_scope)

    service = MCPEffectiveConnectorService(
        template_repo=TemplateRepo(),  # type: ignore[arg-type]
        connector_repo=ConnectorRepo(),  # type: ignore[arg-type]
        state_repo=StateRepo(),  # type: ignore[arg-type]
        grant_repo=GrantRepo(),  # type: ignore[arg-type]
        org_id=org_id,
    )
    rows = await service.list_for_workspace_user(workspace_id, user_id)

    assert len(rows) == 1
    assert rows[0].credential_policy == "workspace"
    assert rows[0].credential_availability == "available"
    assert rows[0].credential_source == "workspace"
    assert rows[0].credential_availability_by_scope == {
        "org": True,
        "workspace": True,
        "user": True,
    }
