"""Tests for :class:`cubebox.mcp.effective.MCPEffectiveConnectorService`.

The service is the only join point between the four-layer tables. These
tests exercise the workspace-visibility rules: workspace-local installs
must not leak across sibling workspaces; org installs must require a
state row to surface in a workspace; uninstalled rows are tombstones.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.mcp.effective import MCPEffectiveConnectorService
from cubebox.models import MCPConnectorInstall
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
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
        install_repo=MCPConnectorInstallRepository(session, org_id=org_id),
        state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=org_id),
        grant_repo=MCPCredentialGrantRepository(session, org_id=org_id),
        org_id=org_id,
    )


async def _add_workspace_install(
    session: AsyncSession,
    *,
    install_id: str,
    org_id: str,
    workspace_id: str,
    install_state: str = "active",
    auth_method: str = "none",
    credential_policy: str = "none",
    auth_status: str = "not_required",
) -> MCPConnectorInstall:
    install = MCPConnectorInstall(
        id=install_id,
        org_id=org_id,
        workspace_id=workspace_id,
        install_scope="workspace",
        template_id=None,
        name=f"ws-install-{install_id}",
        server_url=f"https://mcp.example/{install_id}",
        server_url_hash=install_id,
        transport="streamable_http",
        auth_method=auth_method,
        default_credential_policy=credential_policy,
        auth_status=auth_status,
        install_state=install_state,
        created_by_user_id="u1",
    )
    session.add(install)
    await session.commit()
    await session.refresh(install)
    return install


def _connector_id_for_install(install_id: str) -> str:
    return f"mcpco-{install_id.removeprefix('mcins-')}"


async def _add_org_install(
    session: AsyncSession,
    *,
    install_id: str,
    org_id: str,
    install_state: str = "active",
) -> MCPConnectorInstall:
    install = MCPConnectorInstall(
        id=install_id,
        org_id=org_id,
        workspace_id=None,
        install_scope="org",
        template_id=None,
        name=f"org-install-{install_id}",
        server_url=f"https://mcp.example/org/{install_id}",
        server_url_hash=install_id,
        transport="streamable_http",
        auth_method="none",
        default_credential_policy="none",
        auth_status="not_required",
        install_state=install_state,
        created_by_user_id="u1",
    )
    session.add(install)
    await session.commit()
    await session.refresh(install)
    return install


async def test_workspace_local_install_only_visible_to_owning_workspace(
    session: AsyncSession,
) -> None:
    """A workspace-scope install in ws-A is invisible to ws-B in the same org."""
    org_id = "org-1"
    await _add_workspace_install(
        session,
        install_id="mcins-ws-a-1",
        org_id=org_id,
        workspace_id="ws-a",
    )

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    await state_repo.upsert_for_connector(
        workspace_id="ws-a",
        install_id="mcins-ws-a-1",
        connector_id=_connector_id_for_install("mcins-ws-a-1"),
        enabled=True,
        credential_policy="none",
        enablement_source="workspace_manual",
        updated_by_user_id="u1",
    )

    service = _make_service(session, org_id=org_id)

    rows_a = await service.list_for_workspace_user("ws-a", "u1")
    assert [row.install.id for row in rows_a] == ["mcins-ws-a-1"]
    assert rows_a[0].usable is True

    rows_b = await service.list_for_workspace_user("ws-b", "u1")
    assert rows_b == []


async def test_org_install_requires_workspace_state_to_surface(
    session: AsyncSession,
) -> None:
    """Org install + state row in ws-A is visible to ws-A but not ws-B."""
    org_id = "org-1"
    await _add_org_install(session, install_id="mcins-org-1", org_id=org_id)

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    await state_repo.upsert_for_connector(
        workspace_id="ws-a",
        install_id="mcins-org-1",
        connector_id=_connector_id_for_install("mcins-org-1"),
        enabled=True,
        credential_policy="none",
        enablement_source="admin_manual",
        updated_by_user_id="u1",
    )

    service = _make_service(session, org_id=org_id)

    rows_a = await service.list_for_workspace_user("ws-a", "u1")
    assert [row.install.id for row in rows_a] == ["mcins-org-1"]
    assert rows_a[0].usable is True

    rows_b = await service.list_for_workspace_user("ws-b", "u1")
    assert rows_b == []


async def test_uninstalled_rows_are_filtered(session: AsyncSession) -> None:
    """``install_state='uninstalled'`` rows are tombstones; not surfaced at all."""
    org_id = "org-1"
    await _add_workspace_install(
        session,
        install_id="mcins-active",
        org_id=org_id,
        workspace_id="ws-a",
    )
    await _add_workspace_install(
        session,
        install_id="mcins-tombstone",
        org_id=org_id,
        workspace_id="ws-a",
        install_state="uninstalled",
    )

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    await state_repo.upsert_for_connector(
        workspace_id="ws-a",
        install_id="mcins-active",
        connector_id=_connector_id_for_install("mcins-active"),
        enabled=True,
        credential_policy="none",
        enablement_source="workspace_manual",
        updated_by_user_id="u1",
    )
    await state_repo.upsert_for_connector(
        workspace_id="ws-a",
        install_id="mcins-tombstone",
        connector_id=_connector_id_for_install("mcins-tombstone"),
        enabled=True,
        credential_policy="none",
        enablement_source="workspace_manual",
        updated_by_user_id="u1",
    )

    service = _make_service(session, org_id=org_id)
    rows = await service.list_for_workspace_user("ws-a", "u1")
    assert [row.install.id for row in rows] == ["mcins-active"]


async def test_list_for_workspace_user_excludes_disabled_org_installs(
    session: AsyncSession,
) -> None:
    """include_disabled_org_installs=False drops org installs whose state row is
    disabled (or absent); workspace-scope installs stay visible regardless."""
    org_id = "org-1"
    workspace_id = "ws-1"

    # Workspace-scope install (always visible).
    await _add_workspace_install(
        session,
        install_id="mcins-ws-local",
        org_id=org_id,
        workspace_id=workspace_id,
    )
    # Org installs: one enabled, one disabled, one with no state row.
    await _add_org_install(session, install_id="mcins-org-enabled", org_id=org_id)
    await _add_org_install(session, install_id="mcins-org-disabled", org_id=org_id)
    await _add_org_install(session, install_id="mcins-org-no-state", org_id=org_id)

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    await state_repo.upsert_for_connector(
        workspace_id=workspace_id,
        install_id="mcins-ws-local",
        connector_id=_connector_id_for_install("mcins-ws-local"),
        enabled=True,
        credential_policy="none",
        enablement_source="workspace_manual",
        updated_by_user_id="u1",
    )
    await state_repo.upsert_for_connector(
        workspace_id=workspace_id,
        install_id="mcins-org-enabled",
        connector_id=_connector_id_for_install("mcins-org-enabled"),
        enabled=True,
        credential_policy="none",
        enablement_source="admin_manual",
        updated_by_user_id="u1",
    )
    await state_repo.upsert_for_connector(
        workspace_id=workspace_id,
        install_id="mcins-org-disabled",
        connector_id=_connector_id_for_install("mcins-org-disabled"),
        enabled=False,
        credential_policy="none",
        enablement_source="admin_manual",
        updated_by_user_id="u1",
    )
    # mcins-org-no-state intentionally has no state row.

    service = _make_service(session, org_id=org_id)
    out = await service.list_for_workspace_user(
        workspace_id,
        "u1",
        include_unusable=True,
        include_disabled_org_installs=False,
    )
    ids = {row.install.id for row in out}
    assert "mcins-ws-local" in ids
    assert "mcins-org-enabled" in ids
    assert "mcins-org-disabled" not in ids
    assert "mcins-org-no-state" not in ids


async def test_list_for_workspace_user_default_keeps_disabled_org_installs(
    session: AsyncSession,
) -> None:
    """Backwards compat: default (True) keeps disabled-org rows visible."""
    org_id = "org-1"
    workspace_id = "ws-1"

    await _add_org_install(session, install_id="mcins-org-disabled", org_id=org_id)

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    await state_repo.upsert_for_connector(
        workspace_id=workspace_id,
        install_id="mcins-org-disabled",
        connector_id=_connector_id_for_install("mcins-org-disabled"),
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
    ids = {row.install.id for row in out}
    assert "mcins-org-disabled" in ids


async def test_list_runtime_specs_drops_unusable_rows(session: AsyncSession) -> None:
    """``list_runtime_specs`` returns only usable installs."""
    org_id = "org-1"
    # Usable: workspace install, no auth.
    await _add_workspace_install(
        session,
        install_id="mcins-good",
        org_id=org_id,
        workspace_id="ws-a",
    )
    # Unusable: workspace install with state row missing.
    await _add_workspace_install(
        session,
        install_id="mcins-no-state",
        org_id=org_id,
        workspace_id="ws-a",
    )
    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    await state_repo.upsert_for_connector(
        workspace_id="ws-a",
        install_id="mcins-good",
        connector_id=_connector_id_for_install("mcins-good"),
        enabled=True,
        credential_policy="none",
        enablement_source="workspace_manual",
        updated_by_user_id="u1",
    )

    service = _make_service(session, org_id=org_id)
    specs = await service.list_runtime_specs("ws-a", "u1")
    assert [spec.install_id for spec in specs] == ["mcins-good"]
    assert specs[0].name == "ws-install-mcins-good"
    assert specs[0].auth_method == "none"
    assert specs[0].transport == "streamable_http"
