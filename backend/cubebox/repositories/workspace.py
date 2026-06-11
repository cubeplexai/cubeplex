"""Workspace repository — not org-scoped at row level (workspace IS the scope)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import Workspace


class WorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, org_id: str, name: str) -> Workspace:
        ws = Workspace(org_id=org_id, name=name)
        self.session.add(ws)
        await self.session.commit()
        await self.session.refresh(ws)
        return ws

    async def get(self, workspace_id: str) -> Workspace | None:
        stmt = select(Workspace).where(Workspace.id == workspace_id)  # type: ignore[arg-type]
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def update_name(self, workspace_id: str, name: str) -> Workspace | None:
        ws = await self.get(workspace_id)
        if ws is None:
            return None
        ws.name = name
        self.session.add(ws)
        await self.session.commit()
        await self.session.refresh(ws)
        return ws

    async def list_for_org(self, org_id: str) -> list[Workspace]:
        stmt = select(Workspace).where(Workspace.org_id == org_id)  # type: ignore[arg-type]
        return list((await self.session.execute(stmt)).scalars().all())
