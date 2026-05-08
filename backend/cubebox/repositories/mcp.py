"""MCP connector repositories."""

from datetime import UTC, datetime
from types import EllipsisType
from typing import Any, cast

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import (
    MCPServer,
    UserMCPCredential,
    WorkspaceMCPCredential,
    WorkspaceMCPOverride,
)


class MCPServerRepository:
    """Org-scoped repository for MCP server rows."""

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, server_id: str) -> MCPServer | None:
        stmt = select(MCPServer).where(
            MCPServer.id == server_id,  # type: ignore[arg-type]
            MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_org(
        self,
        *,
        owner_workspace_id: str | None | EllipsisType = ...,
    ) -> list[MCPServer]:
        stmt = select(MCPServer).where(MCPServer.org_id == self.org_id)  # type: ignore[arg-type]
        if owner_workspace_id is not Ellipsis:
            stmt = stmt.where(MCPServer.owner_workspace_id == owner_workspace_id)  # type: ignore[arg-type]
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_workspace(self, workspace_id: str) -> list[MCPServer]:
        """Servers visible to ``workspace_id``.

        Combines:
        - workspace-private installs (``owner_workspace_id == workspace_id``,
          ``authed=true``)
        - org-wide installs (``owner_workspace_id IS NULL``, ``authed=true``)
          NOT explicitly disabled by a ``workspace_mcp_overrides`` row
          for this workspace.
        """
        # Sub-select of disabled override server ids for this workspace.
        disabled_subq = (
            select(cast(Any, WorkspaceMCPOverride.mcp_server_id))
            .where(
                WorkspaceMCPOverride.org_id == self.org_id,  # type: ignore[arg-type]
                WorkspaceMCPOverride.workspace_id == workspace_id,  # type: ignore[arg-type]
                cast(Any, WorkspaceMCPOverride.enabled).is_(False),
            )
            .scalar_subquery()
        )

        stmt = select(MCPServer).where(
            MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
            cast(Any, MCPServer.authed).is_(True),
            or_(
                MCPServer.owner_workspace_id == workspace_id,  # type: ignore[arg-type]
                and_(
                    MCPServer.owner_workspace_id.is_(None),  # type: ignore[union-attr]
                    cast(Any, MCPServer.id).notin_(disabled_subq),
                ),
            ),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def add(self, server: MCPServer) -> MCPServer:
        server.org_id = self.org_id
        self.session.add(server)
        await self.session.commit()
        await self.session.refresh(server)
        return server

    async def update(self, server: MCPServer) -> MCPServer:
        server.updated_at = datetime.now(UTC)
        self.session.add(server)
        await self.session.commit()
        await self.session.refresh(server)
        return server

    async def delete(self, server_id: str) -> None:
        server = await self.get(server_id)
        if server is None:
            return
        await self.session.delete(server)
        await self.session.commit()

    async def find_by_url_hash(
        self,
        *,
        owner_workspace_id: str | None,
        server_url_hash: str,
    ) -> MCPServer | None:
        stmt = select(MCPServer).where(
            MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
            MCPServer.owner_workspace_id == owner_workspace_id,  # type: ignore[arg-type]
            MCPServer.server_url_hash == server_url_hash,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_by_credential_id(self, credential_id: str) -> list[MCPServer]:
        stmt = select(MCPServer).where(
            MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
            MCPServer.credential_id == credential_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_org_wide_with_workspace_override(
        self, workspace_id: str
    ) -> list[tuple[MCPServer, WorkspaceMCPOverride | None]]:
        """Org-wide servers (owner_workspace_id IS NULL) joined with this workspace's
        override row, if any. Replaces the legacy bindings join."""
        stmt = (
            select(MCPServer, WorkspaceMCPOverride)
            .outerjoin(
                WorkspaceMCPOverride,
                (WorkspaceMCPOverride.mcp_server_id == MCPServer.id)  # type: ignore[arg-type]
                & (WorkspaceMCPOverride.workspace_id == workspace_id),
            )
            .where(
                MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
                MCPServer.owner_workspace_id.is_(None),  # type: ignore[union-attr]
            )
        )
        rows = (await self.session.execute(stmt)).all()
        return [(srv, override) for srv, override in rows]


class WorkspaceMCPCredentialRepository:
    """Org-scoped repository for workspace MCP credentials."""

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(
        self,
        *,
        workspace_id: str,
        mcp_server_id: str,
    ) -> WorkspaceMCPCredential | None:
        stmt = select(WorkspaceMCPCredential).where(
            WorkspaceMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPCredential.workspace_id == workspace_id,  # type: ignore[arg-type]
            WorkspaceMCPCredential.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, row: WorkspaceMCPCredential) -> WorkspaceMCPCredential:
        row.org_id = self.org_id
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, *, workspace_id: str, mcp_server_id: str) -> None:
        row = await self.get(workspace_id=workspace_id, mcp_server_id=mcp_server_id)
        if row is None:
            return
        await self.session.delete(row)
        await self.session.commit()

    async def list_for_server(self, mcp_server_id: str) -> list[WorkspaceMCPCredential]:
        stmt = select(WorkspaceMCPCredential).where(
            WorkspaceMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPCredential.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def find_by_credential_id(self, credential_id: str) -> list[WorkspaceMCPCredential]:
        stmt = select(WorkspaceMCPCredential).where(
            WorkspaceMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPCredential.credential_id == credential_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())


class UserMCPCredentialRepository:
    """Org-scoped repository for user MCP credentials."""

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(
        self,
        *,
        user_id: str,
        mcp_server_id: str,
    ) -> UserMCPCredential | None:
        stmt = select(UserMCPCredential).where(
            UserMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            UserMCPCredential.user_id == user_id,  # type: ignore[arg-type]
            UserMCPCredential.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, row: UserMCPCredential) -> UserMCPCredential:
        row.org_id = self.org_id
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, *, user_id: str, mcp_server_id: str) -> None:
        row = await self.get(user_id=user_id, mcp_server_id=mcp_server_id)
        if row is None:
            return
        await self.session.delete(row)
        await self.session.commit()

    async def list_for_server(self, mcp_server_id: str) -> list[UserMCPCredential]:
        stmt = select(UserMCPCredential).where(
            UserMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            UserMCPCredential.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def find_by_credential_id(self, credential_id: str) -> list[UserMCPCredential]:
        stmt = select(UserMCPCredential).where(
            UserMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            UserMCPCredential.credential_id == credential_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())


class WorkspaceMCPOverrideRepository:
    """Org-scoped repository for workspace MCP overrides.

    A row exists only when a workspace explicitly disables an inherited
    org-wide install. Absent row = inherit org-wide default (visible).
    """

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get_for_workspace_and_server(
        self,
        *,
        workspace_id: str,
        mcp_server_id: str,
    ) -> WorkspaceMCPOverride | None:
        stmt = select(WorkspaceMCPOverride).where(
            WorkspaceMCPOverride.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPOverride.workspace_id == workspace_id,  # type: ignore[arg-type]
            WorkspaceMCPOverride.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_workspace(self, workspace_id: str) -> list[WorkspaceMCPOverride]:
        stmt = select(WorkspaceMCPOverride).where(
            WorkspaceMCPOverride.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPOverride.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_server(self, mcp_server_id: str) -> list[WorkspaceMCPOverride]:
        stmt = select(WorkspaceMCPOverride).where(
            WorkspaceMCPOverride.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPOverride.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def upsert(
        self,
        *,
        workspace_id: str,
        mcp_server_id: str,
        enabled: bool,
        updated_by_user_id: str,
    ) -> WorkspaceMCPOverride:
        existing = await self.get_for_workspace_and_server(
            workspace_id=workspace_id,
            mcp_server_id=mcp_server_id,
        )
        if existing is not None:
            existing.enabled = enabled
            existing.updated_by_user_id = updated_by_user_id
            existing.updated_at = datetime.now(UTC)
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = WorkspaceMCPOverride(
            org_id=self.org_id,
            workspace_id=workspace_id,
            mcp_server_id=mcp_server_id,
            enabled=enabled,
            updated_by_user_id=updated_by_user_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, *, workspace_id: str, mcp_server_id: str) -> None:
        row = await self.get_for_workspace_and_server(
            workspace_id=workspace_id,
            mcp_server_id=mcp_server_id,
        )
        if row is None:
            return
        await self.session.delete(row)
        await self.session.commit()
