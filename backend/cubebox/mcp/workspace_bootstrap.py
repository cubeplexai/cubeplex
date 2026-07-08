"""Bootstrap helpers for inheriting org-scope MCP installs into new workspaces.

When a workspace is created, any active org-scope ``MCPConnectorInstall``
in the same org whose ``auto_enroll_new_workspaces`` flag is True gets a
``MCPWorkspaceConnectorState`` row with ``enabled=True`` and
``enablement_source="org_auto_enroll"`` so the new workspace can see the
connector out of the box.

Called from workspace-creation paths (register bootstrap + ``POST /workspaces``).
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.mcp._constants import slugify_for_namespace
from cubebox.models import MCPConnectorInstall
from cubebox.repositories.mcp import MCPConnectorRepository, MCPWorkspaceConnectorStateRepository


async def enroll_workspace_in_org_wide_mcp(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    actor_user_id: str,
) -> None:
    """Upsert enabled connector states for every active auto-enroll org install.

    Iterates org-scope ``mcp_connector_installs`` rows (``workspace_id IS NULL``
    and ``install_state='active'``) whose ``auto_enroll_new_workspaces`` is
    True; idempotent against repeat invocation (the state repo's upsert flips
    an existing row rather than duplicating it).
    """
    stmt = select(MCPConnectorInstall).where(
        MCPConnectorInstall.org_id == org_id,  # type: ignore[arg-type]
        MCPConnectorInstall.workspace_id.is_(None),  # type: ignore[union-attr]
        MCPConnectorInstall.install_state == "active",  # type: ignore[arg-type]
        cast(Any, MCPConnectorInstall.auto_enroll_new_workspaces).is_(True),
    )
    installs = list((await session.execute(stmt)).scalars().all())
    if not installs:
        return

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    connector_repo = MCPConnectorRepository(session, org_id=org_id)
    for install in installs:
        connector = await connector_repo.get_active_by_identity(
            template_id=install.template_id,
            server_url_hash=install.server_url_hash,
            slug_name=slugify_for_namespace(install.name),
        )
        if connector is None:
            continue
        await state_repo.upsert_for_connector(
            workspace_id=workspace_id,
            install_id=install.id,
            connector_id=connector.id,
            enabled=True,
            credential_policy=install.default_credential_policy,
            enablement_source="org_auto_enroll",
            updated_by_user_id=actor_user_id,
        )
