"""Memory repository — scope-aware filtering (no OrgScopedMixin)."""

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.memory import (
    MemoryItem,
    MemoryScope,
    MemoryStatus,
    MemoryType,
)


def _strip_trailing_punct(s: str, pattern: str) -> str:
    """Strip trailing punctuation matching ``pattern`` from ``s``."""
    return re.sub(pattern, "", s)


class MemoryRepository:
    """Scope-aware memory repository.

    - personal: filter by owner_user_id (org/workspace ignored)
    - workspace: filter by workspace_id
    - org: filter by org_id
    - all: union of the above for the current request context
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        org_id: str | None,
        workspace_id: str | None,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.org_id = org_id
        self.workspace_id = workspace_id

    async def get(self, memory_id: str) -> MemoryItem | None:
        stmt = select(MemoryItem).where(MemoryItem.id == memory_id)  # type: ignore[arg-type]
        result = await self.session.execute(stmt)
        item = result.scalar_one_or_none()
        if item is None or not self._can_read(item):
            return None
        return item

    def _can_read(self, item: MemoryItem) -> bool:
        if item.scope == MemoryScope.PERSONAL:
            return item.owner_user_id == self.user_id
        if item.scope == MemoryScope.WORKSPACE:
            return item.workspace_id == self.workspace_id
        if item.scope == MemoryScope.ORG:
            return item.org_id == self.org_id
        return False

    def _scope_filter(self, scope: MemoryScope | None) -> Any:
        clauses: list[Any] = []
        if scope is None or scope == MemoryScope.PERSONAL:
            clauses.append(
                (MemoryItem.scope == MemoryScope.PERSONAL)
                & (MemoryItem.owner_user_id == self.user_id)
            )
        if (scope is None or scope == MemoryScope.WORKSPACE) and self.workspace_id:
            clauses.append(
                (MemoryItem.scope == MemoryScope.WORKSPACE)
                & (MemoryItem.workspace_id == self.workspace_id)
            )
        if (scope is None or scope == MemoryScope.ORG) and self.org_id:
            clauses.append(
                (MemoryItem.scope == MemoryScope.ORG) & (MemoryItem.org_id == self.org_id)
            )
        if not clauses:
            return MemoryItem.id == "__never__"  # empty result
        return or_(*clauses)

    async def list(
        self,
        *,
        scope: MemoryScope | None = None,
        type_: MemoryType | None = None,
        status: MemoryStatus = MemoryStatus.ACTIVE,
        q: str | None = None,
        source_conversation_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[MemoryItem]:
        stmt = select(MemoryItem).where(self._scope_filter(scope))
        stmt = stmt.where(MemoryItem.status == status)  # type: ignore[arg-type]
        if type_:
            stmt = stmt.where(MemoryItem.type == type_)  # type: ignore[arg-type]
        if q:
            stmt = stmt.where(MemoryItem.content.ilike(f"%{q}%"))  # type: ignore[attr-defined]
        if source_conversation_id is not None:
            stmt = stmt.where(MemoryItem.source_conversation_id == source_conversation_id)  # type: ignore[arg-type]
        stmt = stmt.order_by(MemoryItem.created_at.asc()).limit(limit).offset(offset)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        *,
        scope: MemoryScope | None = None,
        status: MemoryStatus = MemoryStatus.ACTIVE,
        source_conversation_id: str | None = None,
    ) -> int:
        """Count visible memories. Mirrors `list`'s scope/status filters; intended
        for the conversation chip which only needs a number, not the rows.
        """
        from sqlalchemy import func

        stmt = select(func.count(MemoryItem.id))  # type: ignore[arg-type]
        stmt = stmt.where(self._scope_filter(scope))
        stmt = stmt.where(MemoryItem.status == status)  # type: ignore[arg-type]
        if source_conversation_id is not None:
            stmt = stmt.where(MemoryItem.source_conversation_id == source_conversation_id)  # type: ignore[arg-type]
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def find_exact(
        self, *, scope: MemoryScope, type_: MemoryType, content: str
    ) -> MemoryItem | None:
        """Dedup safety net for identical (scope/type/content).

        This catches *mechanical* duplicates — accidental retries, double-saves
        across main-agent + reflection-agent within the same turn, etc. It is
        NOT a semantic-similarity check; that's the agent's job via memory_
        search before deciding to save.

        Content is normalized before comparison so a trailing punctuation
        difference (the common case: agent A saves "用户喜欢X。" and agent B
        saves "用户喜欢X") doesn't slip through:
        - leading/trailing whitespace trimmed
        - trailing punctuation (CJK + ASCII) stripped

        The Postgres-side normalization uses regexp_replace + btrim so the
        comparison runs server-side; content has no index anyway, so the
        cost is one regex per row in the scope-filtered set.
        """
        from sqlalchemy import func

        # Keep this charset in sync with the SQL pattern below.
        trailing_punct_re = r"[。！？，；、,.!?;:]+$"
        normalized = _strip_trailing_punct(content.strip(), trailing_punct_re)

        stmt = select(MemoryItem).where(
            self._scope_filter(scope),
            MemoryItem.status == MemoryStatus.ACTIVE,  # type: ignore[arg-type]
            MemoryItem.type == type_,  # type: ignore[arg-type]
            func.regexp_replace(
                func.btrim(MemoryItem.content),
                trailing_punct_re,
                "",
            )
            == normalized,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, item: MemoryItem) -> MemoryItem:
        self.session.add(item)
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def update(self, item: MemoryItem) -> MemoryItem:
        item.updated_at = datetime.now(UTC)
        self.session.add(item)
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def bump_updated_at(self, item: MemoryItem, *, by_user_id: str) -> MemoryItem:
        item.updated_at = datetime.now(UTC)
        item.updated_by_user_id = by_user_id
        return await self.update(item)
