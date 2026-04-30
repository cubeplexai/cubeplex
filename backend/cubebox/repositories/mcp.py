"""MCP connector repositories."""

from datetime import UTC, datetime
from types import EllipsisType
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import (
    MCPServer,
    UserMCPCredential,
    WorkspaceMCPBinding,
    WorkspaceMCPCredential,
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
        owned_stmt = select(MCPServer).where(
            MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
            MCPServer.owner_workspace_id == workspace_id,  # type: ignore[arg-type]
            cast(Any, MCPServer.authed).is_(True),
        )
        owned = list((await self.session.execute(owned_stmt)).scalars().all())

        bound_stmt = (
            select(MCPServer)
            .join(WorkspaceMCPBinding, MCPServer.id == WorkspaceMCPBinding.mcp_server_id)  # type: ignore[arg-type]
            .where(
                MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
                MCPServer.owner_workspace_id.is_(None),  # type: ignore[union-attr]
                cast(Any, MCPServer.authed).is_(True),
                WorkspaceMCPBinding.workspace_id == workspace_id,  # type: ignore[arg-type]
                cast(Any, WorkspaceMCPBinding.enabled).is_(True),
            )
        )
        bound = list((await self.session.execute(bound_stmt)).scalars().all())
        return owned + bound

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


class WorkspaceMCPBindingRepository:
    """Org-scoped repository for workspace MCP bindings."""

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(
        self,
        *,
        workspace_id: str,
        mcp_server_id: str,
    ) -> WorkspaceMCPBinding | None:
        stmt = select(WorkspaceMCPBinding).where(
            WorkspaceMCPBinding.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPBinding.workspace_id == workspace_id,  # type: ignore[arg-type]
            WorkspaceMCPBinding.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_server(self, mcp_server_id: str) -> list[WorkspaceMCPBinding]:
        stmt = select(WorkspaceMCPBinding).where(
            WorkspaceMCPBinding.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPBinding.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def add(self, row: WorkspaceMCPBinding) -> WorkspaceMCPBinding:
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

    async def upsert_bulk(
        self,
        *,
        mcp_server_id: str,
        bindings: list[tuple[str, bool]],
        created_by_user_id: str,
    ) -> None:
        existing = {row.workspace_id: row for row in await self.list_for_server(mcp_server_id)}
        incoming = dict(bindings)

        for workspace_id, enabled in incoming.items():
            row = existing.get(workspace_id)
            if row is None:
                self.session.add(
                    WorkspaceMCPBinding(
                        org_id=self.org_id,
                        workspace_id=workspace_id,
                        mcp_server_id=mcp_server_id,
                        enabled=enabled,
                        created_by_user_id=created_by_user_id,
                    )
                )
            else:
                row.enabled = enabled
                row.updated_at = datetime.now(UTC)
                self.session.add(row)

        for workspace_id, row in existing.items():
            if workspace_id not in incoming:
                await self.session.delete(row)

        await self.session.commit()
