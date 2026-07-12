"""Repository for SandboxEnvVar — org-scoped, nullable workspace/user."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import SandboxEnvVar


class SandboxEnvRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, entry_id: str) -> SandboxEnvVar | None:
        stmt = select(SandboxEnvVar).where(
            SandboxEnvVar.id == entry_id,  # type: ignore[arg-type]
            SandboxEnvVar.org_id == self.org_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_scope(
        self,
        *,
        scope: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> list[SandboxEnvVar]:
        stmt = select(SandboxEnvVar).where(
            SandboxEnvVar.org_id == self.org_id,  # type: ignore[arg-type]
            SandboxEnvVar.scope == scope,  # type: ignore[arg-type]
        )
        if scope in ("workspace", "user"):
            stmt = stmt.where(SandboxEnvVar.workspace_id == workspace_id)  # type: ignore[arg-type]
        if scope == "user":
            stmt = stmt.where(SandboxEnvVar.user_id == user_id)  # type: ignore[arg-type]
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_resolution(self, *, workspace_id: str, user_id: str) -> list[SandboxEnvVar]:
        """All entries in this org that could apply to (workspace_id, user_id):
        org-scope (any), workspace-scope for this workspace, user-scope for this
        (workspace, user). Precedence is applied by the resolver, not here."""
        stmt = select(SandboxEnvVar).where(
            SandboxEnvVar.org_id == self.org_id,  # type: ignore[arg-type]
            SandboxEnvVar.status == "valid",  # type: ignore[arg-type]
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [
            r
            for r in rows
            if (r.scope == "org")
            or (r.scope == "workspace" and r.workspace_id == workspace_id)
            or (r.scope == "user" and r.workspace_id == workspace_id and r.user_id == user_id)
        ]

    async def get_in_scope(
        self,
        *,
        scope: str,
        env_name: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> SandboxEnvVar | None:
        stmt = select(SandboxEnvVar).where(
            SandboxEnvVar.org_id == self.org_id,  # type: ignore[arg-type]
            SandboxEnvVar.scope == scope,  # type: ignore[arg-type]
            SandboxEnvVar.env_name == env_name,  # type: ignore[arg-type]
        )
        if scope in ("workspace", "user"):
            stmt = stmt.where(SandboxEnvVar.workspace_id == workspace_id)  # type: ignore[arg-type]
        if scope == "user":
            stmt = stmt.where(SandboxEnvVar.user_id == user_id)  # type: ignore[arg-type]
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, row: SandboxEnvVar) -> SandboxEnvVar:
        row.org_id = self.org_id
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def update(self, row: SandboxEnvVar) -> SandboxEnvVar:
        if row.org_id != self.org_id:
            raise ValueError("cannot update SandboxEnvVar outside the repo's org scope")
        row.updated_at = datetime.now(UTC)
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, entry_id: str) -> None:
        row = await self.get(entry_id)
        if row is None:
            return
        await self.session.delete(row)
        await self.session.commit()
