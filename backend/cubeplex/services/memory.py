"""Memory service — orchestrates repository + write-time screening."""

from dataclasses import dataclass
from datetime import UTC, datetime

from cubeplex.models.memory import (
    MemoryItem,
    MemoryScope,
    MemorySourceType,
    MemoryStatus,
    MemoryType,
)
from cubeplex.repositories.memory import MemoryRepository
from cubeplex.services.memory_screen import screen_shared_content


@dataclass
class CreateMemoryInput:
    scope: MemoryScope
    type: MemoryType
    content: str
    confidence: float = 0.8
    source_type: MemorySourceType = MemorySourceType.MANUAL
    source_conversation_id: str | None = None
    source_run_id: str | None = None
    source_artifact_id: str | None = None
    source_excerpt: str | None = None


class MemoryPermissionError(Exception):
    """Raised when the current user cannot write the requested scope."""


class MemoryService:
    def __init__(
        self,
        repo: MemoryRepository,
        *,
        user_id: str,
        org_id: str | None,
        workspace_id: str | None,
    ) -> None:
        self.repo = repo
        self.user_id = user_id
        self.org_id = org_id
        self.workspace_id = workspace_id

    def _check_write_scope(self, scope: MemoryScope) -> None:
        if scope == MemoryScope.PERSONAL:
            return  # any logged-in user can write their own
        if scope == MemoryScope.WORKSPACE and not self.workspace_id:
            raise MemoryPermissionError("workspace memory requires workspace context")
        if scope == MemoryScope.ORG and not self.org_id:
            raise MemoryPermissionError("org memory requires org context")

    async def create(self, inp: CreateMemoryInput) -> MemoryItem:
        self._check_write_scope(inp.scope)
        if inp.scope in (MemoryScope.WORKSPACE, MemoryScope.ORG):
            screen_shared_content(inp.content)  # raises MemoryScreenError

        # Exact-content dedup
        existing = await self.repo.find_exact(scope=inp.scope, type_=inp.type, content=inp.content)
        if existing is not None:
            return await self.repo.bump_updated_at(existing, by_user_id=self.user_id)

        item = MemoryItem(
            scope=inp.scope,
            org_id=self.org_id if inp.scope != MemoryScope.PERSONAL else None,
            workspace_id=self.workspace_id if inp.scope == MemoryScope.WORKSPACE else None,
            owner_user_id=self.user_id if inp.scope == MemoryScope.PERSONAL else None,
            type=inp.type,
            content=inp.content,
            confidence=inp.confidence,
            source_type=inp.source_type,
            source_conversation_id=inp.source_conversation_id,
            source_run_id=inp.source_run_id,
            source_artifact_id=inp.source_artifact_id,
            source_excerpt=inp.source_excerpt,
            created_by_user_id=self.user_id,
        )
        return await self.repo.add(item)

    async def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        type_: MemoryType | None = None,
        confidence: float | None = None,
        status: MemoryStatus | None = None,
    ) -> MemoryItem:
        item = await self.repo.get(memory_id)
        if item is None:
            raise LookupError("memory item not found or not accessible")
        if content is not None:
            if item.scope in (MemoryScope.WORKSPACE, MemoryScope.ORG):
                screen_shared_content(content)
            item.content = content
        if type_ is not None:
            item.type = type_
        if confidence is not None:
            item.confidence = confidence
        if status is not None:
            item.status = status
        item.updated_by_user_id = self.user_id
        return await self.repo.update(item)

    async def archive(self, memory_id: str) -> MemoryItem:
        return await self.update(memory_id, status=MemoryStatus.ARCHIVED)

    async def touch_used(self, memory_id: str) -> None:
        item = await self.repo.get(memory_id)
        if item is None:
            return
        item.last_used_at = datetime.now(UTC)
        await self.repo.update(item)
