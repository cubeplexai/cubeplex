"""Bootstrap helpers for inheriting org MCP connectors into new workspaces."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.repositories.mcp import MCPConnectorRepository, MCPWorkspaceConnectorStateRepository


async def enroll_workspace_in_org_wide_mcp(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    actor_user_id: str,
) -> None:
    """Upsert enabled connector states for every active auto-enroll connector."""
    connector_repo = MCPConnectorRepository(session, org_id=org_id)
    connectors = await connector_repo.list_auto_enroll_active()
    if not connectors:
        return

    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org_id)
    for connector in connectors:
        await state_repo.upsert_for_connector(
            workspace_id=workspace_id,
            connector_id=connector.id,
            enabled=True,
            credential_policy=connector.default_credential_policy,
            enablement_source="org_auto_enroll",
            updated_by_user_id=actor_user_id,
        )
