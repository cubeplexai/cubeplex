"""Unit tests for OrgScopedMixin and ScopedRepository."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import Field, SQLModel

from cubeplex.models.mixins import OrgScopedMixin
from cubeplex.repositories.base import ScopedRepository


class _Item(SQLModel, OrgScopedMixin, table=True):
    __tablename__ = "_test_items"
    id: str = Field(primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class _ItemRepo(ScopedRepository[_Item]):
    model = _Item


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_scoped_repo_filters_by_org_and_workspace(session):
    s = session
    s.add(_Item(id="i1", org_id="o1", workspace_id="w1", name="a"))
    s.add(_Item(id="i2", org_id="o1", workspace_id="w2", name="b"))
    s.add(_Item(id="i3", org_id="o2", workspace_id="w1", name="c"))
    await s.commit()

    repo = _ItemRepo(s, org_id="o1", workspace_id="w1")
    items = await repo.list()
    assert {i.id for i in items} == {"i1"}


async def test_scoped_repo_get_by_id_enforces_scope(session):
    s = session
    s.add(_Item(id="i1", org_id="o1", workspace_id="w1", name="a"))
    await s.commit()

    repo_in_scope = _ItemRepo(s, org_id="o1", workspace_id="w1")
    repo_wrong_ws = _ItemRepo(s, org_id="o1", workspace_id="w2")

    assert (await repo_in_scope.get("i1")) is not None
    assert (await repo_wrong_ws.get("i1")) is None


async def test_scoped_repo_add_force_sets_scope(session):
    s = session
    repo = _ItemRepo(s, org_id="o1", workspace_id="w1")
    # Caller tries to sneak a different scope onto the object
    item = _Item(id="i1", org_id="o-evil", workspace_id="w-evil", name="a")
    await repo.add(item)

    # Repo must have overwritten the scope to its own
    assert item.org_id == "o1"
    assert item.workspace_id == "w1"

    # And only the in-scope repo can read it back
    assert (await repo.get("i1")) is not None
    wrong = _ItemRepo(s, org_id="o-evil", workspace_id="w-evil")
    assert (await wrong.get("i1")) is None


async def test_scoped_repo_delete_respects_scope(session):
    s = session
    s.add(_Item(id="i1", org_id="o1", workspace_id="w1", name="a"))
    await s.commit()

    repo_wrong = _ItemRepo(s, org_id="o1", workspace_id="w2")
    assert (await repo_wrong.delete("i1")) is False

    repo_right = _ItemRepo(s, org_id="o1", workspace_id="w1")
    assert (await repo_right.delete("i1")) is True
    assert (await repo_right.get("i1")) is None
