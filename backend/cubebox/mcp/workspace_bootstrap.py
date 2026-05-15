"""Bootstrap helpers for inheriting org-wide MCP installs into new workspaces.

When a workspace is created, any org-wide MCP server in the same org whose
``auto_enroll_new_workspaces`` flag is True gets a ``WorkspaceMCPOverride``
row with ``enabled=True`` so the new workspace can see the connector out of
the box.

Called from workspace-creation paths (register bootstrap + ``POST /workspaces``).
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import MCPServer
from cubebox.repositories.mcp import WorkspaceMCPOverrideRepository


async def enroll_workspace_in_org_wide_mcp(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    actor_user_id: str,
) -> None:
    """Upsert enabled overrides for every auto-enroll org-wide install.

    Iterates org-wide ``mcp_servers`` rows (``owner_workspace_id IS NULL``)
    whose ``auto_enroll_new_workspaces`` is True; idempotent against repeat
    invocation (the override repo's upsert flips an existing row rather than
    duplicating it).
    """
    stmt = select(MCPServer).where(
        MCPServer.org_id == org_id,  # type: ignore[arg-type]
        MCPServer.owner_workspace_id.is_(None),  # type: ignore[union-attr]
        cast(Any, MCPServer.auto_enroll_new_workspaces).is_(True),
    )
    servers = list((await session.execute(stmt)).scalars().all())
    if not servers:
        return

    override_repo = WorkspaceMCPOverrideRepository(session, org_id=org_id)
    for server in servers:
        await override_repo.upsert(
            workspace_id=workspace_id,
            mcp_server_id=server.id,
            enabled=True,
            updated_by_user_id=actor_user_id,
        )
