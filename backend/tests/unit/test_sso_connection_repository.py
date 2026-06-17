"""Tests for the org-scoped SSOConnectionRepository."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.models.sso_connection import SSOConnection
from cubebox.repositories.sso_connection import SSOConnectionRepository


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _make_connection(org_id: str = "org-1") -> SSOConnection:
    return SSOConnection(
        org_id=org_id,
        protocol="oidc",
        display_name="Acme OIDC",
        status="active",
        provisioning="auto",
        config={"issuer": "https://idp.example"},
    )


async def test_add_pins_org_id_and_returns_persisted(session: AsyncSession) -> None:
    repo = SSOConnectionRepository(session, org_id="org-1")

    # Even if caller mis-sets org_id, ``add`` overwrites with the repo's org.
    saved = await repo.add(_make_connection(org_id="org-evil"))

    assert saved.org_id == "org-1"
    assert saved.id.startswith("sso-")


async def test_get_returns_only_this_orgs_connection(session: AsyncSession) -> None:
    repo_1 = SSOConnectionRepository(session, org_id="org-1")
    repo_2 = SSOConnectionRepository(session, org_id="org-2")

    await repo_1.add(_make_connection())

    assert await repo_1.get() is not None
    assert await repo_2.get() is None


async def test_get_by_id_enforces_org_scope(session: AsyncSession) -> None:
    repo_1 = SSOConnectionRepository(session, org_id="org-1")
    saved = await repo_1.add(_make_connection())

    assert await repo_1.get_by_id(saved.id) == saved
    assert await SSOConnectionRepository(session, org_id="org-2").get_by_id(saved.id) is None


async def test_update_bumps_updated_at(session: AsyncSession) -> None:
    repo = SSOConnectionRepository(session, org_id="org-1")
    saved = await repo.add(_make_connection())
    original_updated_at = saved.updated_at

    saved.display_name = "Acme OIDC v2"
    updated = await repo.update(saved)

    assert updated.display_name == "Acme OIDC v2"
    assert updated.updated_at >= original_updated_at


async def test_delete_returns_true_when_present_and_scoped(session: AsyncSession) -> None:
    repo_1 = SSOConnectionRepository(session, org_id="org-1")
    saved = await repo_1.add(_make_connection())

    # Other org cannot delete it.
    other_deleted = await SSOConnectionRepository(session, org_id="org-2").delete(saved.id)
    assert other_deleted is False
    assert await repo_1.get_by_id(saved.id) is not None

    # Owning org can.
    deleted = await repo_1.delete(saved.id)
    assert deleted is True
    assert await repo_1.get_by_id(saved.id) is None


async def test_delete_missing_id_returns_false(session: AsyncSession) -> None:
    repo = SSOConnectionRepository(session, org_id="org-1")
    assert await repo.delete("sso-does-not-exist") is False
