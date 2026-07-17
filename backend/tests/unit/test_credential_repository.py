"""Tests for the org-scoped CredentialRepository."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubeplex.models import Credential


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_credential_repository_enforces_org_scope(session: AsyncSession) -> None:
    from cubeplex.repositories.credential import CredentialRepository

    repo = CredentialRepository(session, org_id="org-1")
    cred = Credential(
        org_id="org-evil",
        kind="mcp_server",
        name="github",
        value_encrypted=b"ciphertext",
        created_by_user_id="user-1",
    )

    saved = await repo.add(cred)

    assert saved.org_id == "org-1"
    assert await repo.get(saved.id) == saved
    assert await CredentialRepository(session, org_id="org-2").get(saved.id) is None


async def test_credential_repository_delete_respects_org_scope(session: AsyncSession) -> None:
    from cubeplex.repositories.credential import CredentialRepository

    repo = CredentialRepository(session, org_id="org-1")
    saved = await repo.add(
        Credential(
            org_id="org-1",
            kind="mcp_server",
            name="github",
            value_encrypted=b"ciphertext",
            created_by_user_id="user-1",
        )
    )

    await CredentialRepository(session, org_id="org-2").delete(saved.id)

    assert await repo.get(saved.id) is not None

    await repo.delete(saved.id)

    assert await repo.get(saved.id) is None
