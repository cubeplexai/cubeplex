"""Base repository that auto-scopes queries by (org_id, workspace_id)."""

from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel


class ScopedRepository[T: SQLModel]:
    """Repository base that injects WHERE org_id=? AND workspace_id=? on every query.

    Subclasses set `model = SomeModel` (must inherit OrgScopedMixin).
    Pass org_id and workspace_id at construction (resolved from RequestContext).
    """

    model: ClassVar[type[SQLModel]]

    def __init__(self, session: AsyncSession, *, org_id: str, workspace_id: str) -> None:
        if not hasattr(self.model, "org_id") or not hasattr(self.model, "workspace_id"):
            raise TypeError(
                f"{self.model.__name__} must inherit OrgScopedMixin to use ScopedRepository"
            )
        self.session = session
        self.org_id = org_id
        self.workspace_id = workspace_id

    def _scoped_select(self) -> Any:
        return select(self.model).where(
            self.model.org_id == self.org_id,  # type: ignore[attr-defined]
            self.model.workspace_id == self.workspace_id,  # type: ignore[attr-defined]
        )

    async def get(self, id_: str) -> T | None:
        stmt = self._scoped_select().where(self.model.id == id_)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[T]:
        stmt = self._scoped_select().limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, obj: T) -> T:
        # Force-set scope columns so callers cannot leak across workspaces
        obj.org_id = self.org_id
        obj.workspace_id = self.workspace_id
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def delete(self, id_: str) -> bool:
        obj = await self.get(id_)
        if obj is None:
            return False
        await self.session.delete(obj)
        await self.session.commit()
        return True
