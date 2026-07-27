"""Memory repository — scope-aware filtering (no OrgScopedMixin)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.memory import (
    MemoryItem,
    MemoryScope,
    MemoryStatus,
    MemoryType,
)

# Trailing punctuation stripped for L0 exact dedup (keep in sync with SQL below).
_TRAILING_PUNCT_RE = r"[。！？，；、,.!?;:]+$"


def normalize_memory_content(content: str) -> str:
    """L0 normalize for exact dedup: trim, collapse whitespace, strip trailing punct."""
    collapsed = re.sub(r"\s+", " ", content.strip())
    return re.sub(_TRAILING_PUNCT_RE, "", collapsed).strip()


class MemoryRepository:
    """Scope-aware memory repository.

    - personal: owner_user_id + current workspace_id (orphans with NULL ws excluded)
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
            # Orphans (workspace_id NULL) are not injectable/readable in-app.
            return (
                item.owner_user_id == self.user_id
                and self.workspace_id is not None
                and item.workspace_id == self.workspace_id
            )
        if item.scope == MemoryScope.WORKSPACE:
            return item.workspace_id == self.workspace_id
        if item.scope == MemoryScope.ORG:
            return item.org_id == self.org_id
        return False

    def _scope_filter(self, scope: MemoryScope | None) -> Any:
        clauses: list[Any] = []
        if (scope is None or scope == MemoryScope.PERSONAL) and self.workspace_id:
            clauses.append(
                (MemoryItem.scope == MemoryScope.PERSONAL)
                & (MemoryItem.owner_user_id == self.user_id)
                & (MemoryItem.workspace_id == self.workspace_id)
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
        order_by_recent: bool = False,
    ) -> list[MemoryItem]:
        stmt = select(MemoryItem).where(self._scope_filter(scope))
        stmt = stmt.where(MemoryItem.status == status)  # type: ignore[arg-type]
        if type_:
            stmt = stmt.where(MemoryItem.type == type_)  # type: ignore[arg-type]
        if q:
            stmt = stmt.where(MemoryItem.content.ilike(f"%{q}%"))  # type: ignore[attr-defined]
        if source_conversation_id is not None:
            stmt = stmt.where(MemoryItem.source_conversation_id == source_conversation_id)  # type: ignore[arg-type]
        if order_by_recent:
            # last_used_at DESC NULLS LAST, created_at DESC — used by reflection to
            # fetch the most recently relevant items when there are many memories.
            stmt = stmt.order_by(
                MemoryItem.last_used_at.desc().nulls_last(),  # type: ignore[union-attr]
                MemoryItem.created_at.desc(),  # type: ignore[attr-defined]
            )
        else:
            stmt = stmt.order_by(MemoryItem.created_at.asc())  # type: ignore[attr-defined]
        stmt = stmt.limit(limit).offset(offset)
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
        """L0 exact dedup within the current scope key (scope/type + mount).

        Catches mechanical duplicates (retries, double-save). Not semantic.
        Normalization: strip, collapse whitespace, strip trailing punctuation.
        """
        from sqlalchemy import func

        normalized = normalize_memory_content(content)

        # SQL mirror of normalize_memory_content:
        # btrim → collapse ws → strip trailing punct → btrim again.
        content_norm = func.btrim(
            func.regexp_replace(
                func.regexp_replace(func.btrim(MemoryItem.content), r"\s+", " ", "g"),
                _TRAILING_PUNCT_RE,
                "",
            )
        )

        stmt = select(MemoryItem).where(
            self._scope_filter(scope),
            MemoryItem.status == MemoryStatus.ACTIVE,  # type: ignore[arg-type]
            MemoryItem.type == type_,  # type: ignore[arg-type]
            content_norm == normalized,
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

    async def touch_used_many(self, memory_ids: Sequence[str]) -> None:
        """Best-effort batch update of last_used_at for injected memory ids.

        Does not re-check scope read ACL beyond the ids list (caller only
        passes ids it just selected from this repo's list). Empty input is a
        no-op. Failures should not block injection; callers may swallow.

        Note: parameter type uses ``Sequence`` (not ``list``) because this
        class defines a method named ``list`` that would shadow the builtin
        in annotations for mypy.
        """
        if not memory_ids:
            return
        now = datetime.now(UTC)
        # Distinct preserve order while avoiding duplicate updates.
        # Avoid annotating with ``list[str]`` — method ``list`` shadows builtin for mypy.
        seen: set[str] = set()
        unique_ids = []
        for mid in memory_ids:
            if mid and mid not in seen:
                seen.add(mid)
                unique_ids.append(mid)
        stmt = select(MemoryItem).where(MemoryItem.id.in_(unique_ids))  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        rows = result.scalars().all()
        if not rows:
            return
        for row in rows:
            row.last_used_at = now
            self.session.add(row)
        await self.session.commit()

    async def find_eviction_candidate(
        self, *, scope: MemoryScope = MemoryScope.PERSONAL
    ) -> MemoryItem | None:
        """Pick the least-valuable active item for soft-cap eviction.

        Prefer never-used, oldest created, non-correction first.
        """
        items = await self.list(scope=scope, status=MemoryStatus.ACTIVE, limit=200)
        if not items:
            return None
        items.sort(
            key=lambda m: (
                0 if m.type != MemoryType.CORRECTION else 1,
                0 if m.last_used_at is None else 1,
                m.last_used_at.timestamp() if m.last_used_at else 0.0,
                m.created_at.timestamp(),
            )
        )
        return items[0]
