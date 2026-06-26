"""Repository for UserSandboxSyncEvent — insert + read queries.

Org/workspace scoping is enforced structurally by ScopedRepository.
Manifest snapshots are stored as JSONB; for the rare 'which sandbox has skill
X' lookup admin runs SQL directly (see spec §5.3) — no repo method.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import UserSandboxSyncEvent
from cubebox.repositories.base import ScopedRepository


class UserSandboxSyncEventRepository(ScopedRepository[UserSandboxSyncEvent]):
    model = UserSandboxSyncEvent

    async def create(self, event: UserSandboxSyncEvent) -> str:
        """Insert a new sync event row and return its id."""
        saved = await self.add(event)
        return saved.id

    async def list_for_sandbox(
        self, user_sandbox_id: str, *, limit: int, offset: int
    ) -> list[UserSandboxSyncEvent]:
        stmt = (
            self._scoped_select()
            .where(UserSandboxSyncEvent.user_sandbox_id == user_sandbox_id)
            .order_by(cast(Any, UserSandboxSyncEvent.started_at).desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_scope(
        self,
        *,
        workspace_id: str | None,
        status: str | None,
        since: datetime | None,
        until: datetime | None,
        limit: int,
        offset: int,
    ) -> list[UserSandboxSyncEvent]:
        """Org-scoped listing with optional narrowing filters.

        ``_scoped_select()`` already constrains to the (org_id, workspace_id)
        the repo was constructed with.  The ``workspace_id`` arg adds a second
        equality predicate when the caller wants to narrow further; pass ``None``
        to skip it (org + workspace from construction still apply).
        """
        stmt = self._scoped_select()
        if workspace_id is not None:
            stmt = stmt.where(UserSandboxSyncEvent.workspace_id == workspace_id)
        if status is not None:
            stmt = stmt.where(UserSandboxSyncEvent.status == status)
        if since is not None:
            stmt = stmt.where(UserSandboxSyncEvent.started_at >= since)
        if until is not None:
            stmt = stmt.where(UserSandboxSyncEvent.started_at < until)
        stmt = (
            stmt.order_by(cast(Any, UserSandboxSyncEvent.started_at).desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def list_for_org(
        cls,
        session: AsyncSession,
        *,
        org_id: str,
        workspace_id: str | None,
        status: str | None,
        since: datetime | None,
        until: datetime | None,
        limit: int,
        offset: int,
    ) -> list[UserSandboxSyncEvent]:
        """Org-wide cross-workspace query for admin use.

        When ``workspace_id`` is None, returns events from every workspace in
        the org.  Mirrors ``UserSandboxRepository.list_expired_system`` pattern.
        """
        stmt = select(UserSandboxSyncEvent).where(
            UserSandboxSyncEvent.org_id == org_id,  # type: ignore[arg-type]
        )
        if workspace_id is not None:
            stmt = stmt.where(
                UserSandboxSyncEvent.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
        if status is not None:
            stmt = stmt.where(UserSandboxSyncEvent.status == status)  # type: ignore[arg-type]
        if since is not None:
            stmt = stmt.where(UserSandboxSyncEvent.started_at >= since)  # type: ignore[arg-type]
        if until is not None:
            stmt = stmt.where(UserSandboxSyncEvent.started_at < until)  # type: ignore[arg-type]
        stmt = (
            stmt.order_by(cast(Any, UserSandboxSyncEvent.started_at).desc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
