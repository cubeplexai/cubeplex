"""Bootstrap helpers for inheriting org-wide MCP installs into new workspaces.

When a workspace is created, any org-wide MCP server in the same org whose
``auto_enroll_new_workspaces`` flag is True **and** whose ``authed`` is True
gets a ``WorkspaceMCPOverride`` row with ``enabled=True`` so the new
workspace can see the connector out of the box.

The ``authed`` filter matters: ``delete_install`` is a soft delete that
flips ``authed`` to False but leaves ``auto_enroll_new_workspaces``
untouched (the flag captures the admin's policy intent, not the install's
live state). Without this filter, soft-deleted installs would zombie back
into every newly created workspace as "needs_setup" entries. Pending-OAuth
installs (authed=False, never completed) are also skipped here — those
get backfilled at install time on workspaces that exist *at* install time,
which is the only moment we have a fresh "spread to all workspaces" signal
from the admin. A workspace created during the brief OAuth window can opt
in manually via the override toggle once the install lands.

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
    """Upsert enabled overrides for every authed auto-enroll org-wide install.

    Iterates org-wide ``mcp_servers`` rows (``owner_workspace_id IS NULL``)
    whose ``auto_enroll_new_workspaces`` AND ``authed`` are both True;
    idempotent against repeat invocation (the override repo's upsert flips
    an existing row rather than duplicating it).
    """
    stmt = select(MCPServer).where(
        MCPServer.org_id == org_id,  # type: ignore[arg-type]
        MCPServer.owner_workspace_id.is_(None),  # type: ignore[union-attr]
        cast(Any, MCPServer.auto_enroll_new_workspaces).is_(True),
        cast(Any, MCPServer.authed).is_(True),
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
