"""Unit tests for MemoryService soft-cap and MemoryRepository helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.models.memory import MemoryScope, MemoryStatus, MemoryType
from cubeplex.services.memory import (
    PERSONAL_ACTIVE_SOFT_CAP,
    CreateMemoryInput,
    MemoryPermissionError,
    MemoryService,
)


def _item(
    *,
    mid: str = "mem-1",
    type_: MemoryType = MemoryType.PREFERENCE,
    last_used: datetime | None = None,
    created: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=mid,
        type=type_,
        last_used_at=last_used,
        created_at=created or datetime(2026, 1, 1, tzinfo=UTC),
        scope=MemoryScope.PERSONAL,
        content=f"content-{mid}",
    )


class _FakeRepo:
    def __init__(self) -> None:
        self.items: list[Any] = []
        self.archived: list[str] = []
        self.added: list[Any] = []
        self.touched: list[str] = []

    async def find_exact(self, **_kw: Any) -> None:
        return None

    async def count(self, **_kw: Any) -> int:
        return len([i for i in self.items if getattr(i, "status", MemoryStatus.ACTIVE)])

    async def find_eviction_candidate(self, **_kw: Any) -> Any:
        active = [
            i
            for i in self.items
            if getattr(i, "status", MemoryStatus.ACTIVE) != MemoryStatus.ARCHIVED
        ]
        if not active:
            return None
        active.sort(
            key=lambda m: (
                0 if m.type != MemoryType.CORRECTION else 1,
                0 if m.last_used_at is None else 1,
                m.last_used_at.timestamp() if m.last_used_at else 0.0,
                m.created_at.timestamp(),
            )
        )
        return active[0]

    async def get(self, memory_id: str) -> Any:
        for i in self.items:
            if i.id == memory_id:
                return i
        return None

    async def update(self, item: Any) -> Any:
        return item

    async def add(self, item: Any) -> Any:
        self.added.append(item)
        # Mirror DB-ish defaults
        if not getattr(item, "id", None):
            item.id = f"mem-new-{len(self.added)}"
        self.items.append(item)
        return item

    async def list(self, **_kw: Any) -> list[Any]:
        return list(self.items)


@pytest.mark.asyncio
async def test_personal_create_requires_workspace() -> None:
    repo = _FakeRepo()
    svc = MemoryService(repo, user_id="u1", org_id=None, workspace_id=None)  # type: ignore[arg-type]
    with pytest.raises(MemoryPermissionError, match="workspace"):
        await svc.create(
            CreateMemoryInput(scope=MemoryScope.PERSONAL, type=MemoryType.PREFERENCE, content="x")
        )


@pytest.mark.asyncio
async def test_soft_cap_archives_before_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cubeplex.services.memory.PERSONAL_ACTIVE_SOFT_CAP", 2)
    repo = _FakeRepo()
    # Pre-fill two active personal items
    a = _item(mid="mem-old", created=datetime(2026, 1, 1, tzinfo=UTC))
    b = _item(mid="mem-mid", created=datetime(2026, 1, 2, tzinfo=UTC))
    a.status = MemoryStatus.ACTIVE
    b.status = MemoryStatus.ACTIVE
    repo.items = [a, b]

    svc = MemoryService(repo, user_id="u1", org_id="org-1", workspace_id="ws-1")  # type: ignore[arg-type]

    async def _archive(memory_id: str) -> Any:
        for i in repo.items:
            if i.id == memory_id:
                i.status = MemoryStatus.ARCHIVED
                repo.archived.append(memory_id)
                return i
        raise LookupError(memory_id)

    svc.archive = _archive  # type: ignore[method-assign]

    # Fake MemoryItem construction by making add store CreateMemoryInput-like object
    from cubeplex.models.memory import MemoryItem

    async def _add(item: MemoryItem) -> MemoryItem:
        item.status = MemoryStatus.ACTIVE
        repo.items.append(item)
        repo.added.append(item)
        return item

    repo.add = _add  # type: ignore[method-assign]

    await svc.create(
        CreateMemoryInput(
            scope=MemoryScope.PERSONAL,
            type=MemoryType.PREFERENCE,
            content="brand-new",
        )
    )
    assert "mem-old" in repo.archived
    assert len(repo.added) == 1
    assert repo.added[0].content == "brand-new"
    assert repo.added[0].workspace_id == "ws-1"
    assert repo.added[0].owner_user_id == "u1"
    assert PERSONAL_ACTIVE_SOFT_CAP >= 2  # constant still exists


@pytest.mark.asyncio
async def test_find_eviction_prefers_unused_non_correction() -> None:
    from cubeplex.repositories.memory import MemoryRepository

    repo = MemoryRepository.__new__(MemoryRepository)
    items = [
        _item(
            mid="corr",
            type_=MemoryType.CORRECTION,
            created=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _item(
            mid="used",
            type_=MemoryType.PREFERENCE,
            last_used=datetime(2026, 2, 1, tzinfo=UTC),
            created=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _item(
            mid="unused",
            type_=MemoryType.PREFERENCE,
            last_used=None,
            created=datetime(2026, 1, 3, tzinfo=UTC),
        ),
    ]
    repo.list = AsyncMock(return_value=items)  # type: ignore[method-assign]
    victim = await repo.find_eviction_candidate()
    assert victim is not None
    assert victim.id == "unused"


@pytest.mark.asyncio
async def test_touch_used_many_noops_on_empty() -> None:
    from cubeplex.repositories.memory import MemoryRepository

    repo = MemoryRepository.__new__(MemoryRepository)
    repo.session = MagicMock()
    await repo.touch_used_many([])
    repo.session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_touch_used_many_updates_rows() -> None:
    from cubeplex.repositories.memory import MemoryRepository

    row = _item(mid="mem-x")
    row.last_used_at = None
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    session.add = MagicMock()

    repo = MemoryRepository.__new__(MemoryRepository)
    repo.session = session
    await repo.touch_used_many(["mem-x", "mem-x", ""])
    assert row.last_used_at is not None
    session.add.assert_called_with(row)
    session.commit.assert_awaited_once()
