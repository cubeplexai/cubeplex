"""OrgSettings supports nullable org_id system rows after A7.5 refactor."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select

from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_system_row_with_null_org_id(session: AsyncSession) -> None:
    """Can insert and query an OrgSettings row where org_id IS NULL."""
    row = OrgSettings(org_id=None, key=MODEL_PRESETS_KEY, value={"x": 1})
    session.add(row)
    await session.commit()
    fetched = (await session.execute(select(OrgSettings))).scalar_one()
    assert fetched.org_id is None
    assert fetched.key == MODEL_PRESETS_KEY
    assert fetched.id.startswith("oset-")


@pytest.mark.asyncio
async def test_system_and_org_rows_coexist(session: AsyncSession) -> None:
    """System (org_id=NULL) and org-owned rows can both be inserted.

    SQLite does not honour ``postgresql_where``, so it would treat the
    partial-unique indexes as plain unique indexes and reject the second
    insert if both rows shared ``key``. We therefore use distinct keys —
    the partial-index semantics are verified at the Postgres level by the
    migration applying cleanly (see alembic upgrade in CI / dev).
    """
    session.add(OrgSettings(org_id=None, key="sys_key", value={"who": "system"}))
    session.add(OrgSettings(org_id="org_x", key="org_key", value={"who": "org"}))
    await session.commit()
    rows = (await session.execute(select(OrgSettings))).scalars().all()
    assert len(rows) == 2
    by_owner = {r.org_id: r.value["who"] for r in rows}
    assert by_owner == {None: "system", "org_x": "org"}


@pytest.mark.asyncio
async def test_id_generated_per_row(session: AsyncSession) -> None:
    """Each new row gets a unique public ID with the oset- prefix."""
    # Use distinct keys: SQLite enforces ``uq_org_settings_system_key`` as a
    # plain unique on (key) since it does not honour ``postgresql_where``.
    session.add(OrgSettings(org_id=None, key="key_a", value={}))
    session.add(OrgSettings(org_id=None, key="key_b", value={}))
    await session.commit()
    rows = (await session.execute(select(OrgSettings))).scalars().all()
    ids = {r.id for r in rows}
    assert len(ids) == 2  # both unique
    assert all(i.startswith("oset-") for i in ids)
