"""Tests for CredentialService vault behavior."""

from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.credentials.encryption import FernetBackend
from cubebox.credentials.exceptions import CredentialKindMismatch, CredentialNotFound
from cubebox.repositories.credential import CredentialRepository


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


async def test_credential_service_encrypts_and_decrypts(
    session: AsyncSession, backend: FernetBackend
) -> None:
    from cubebox.services.credential import CredentialService

    repo = CredentialRepository(session, org_id="org-1")
    service = CredentialService(repo, backend, org_id="org-1", actor_user_id="user-1")

    credential_id = await service.create(
        kind="mcp_server",
        name="github",
        plaintext="ghp_secret",
        metadata={"source": "test"},
    )
    stored = await repo.get(credential_id)

    assert stored is not None
    assert stored.value_encrypted != b"ghp_secret"
    assert stored.cred_metadata == {"source": "test"}
    assert (
        await service.get_decrypted(
            credential_id=credential_id,
            requesting_kind="mcp_server",
        )
        == "ghp_secret"
    )


async def test_credential_service_rejects_kind_mismatch(
    session: AsyncSession, backend: FernetBackend
) -> None:
    from cubebox.services.credential import CredentialService

    service = CredentialService(
        CredentialRepository(session, org_id="org-1"),
        backend,
        org_id="org-1",
        actor_user_id="user-1",
    )
    credential_id = await service.create(kind="mcp_server", name="github", plaintext="secret")

    with pytest.raises(CredentialKindMismatch):
        await service.get_decrypted(credential_id=credential_id, requesting_kind="skill_env")


async def test_credential_service_update_and_delete(
    session: AsyncSession, backend: FernetBackend
) -> None:
    from cubebox.services.credential import CredentialService

    service = CredentialService(
        CredentialRepository(session, org_id="org-1"),
        backend,
        org_id="org-1",
        actor_user_id="user-1",
    )
    credential_id = await service.create(kind="mcp_server", name="old", plaintext="old-secret")

    await service.update(
        credential_id=credential_id,
        plaintext="new-secret",
        name="new",
        metadata={"rotated": True},
    )

    assert (
        await service.get_decrypted(
            credential_id=credential_id,
            requesting_kind="mcp_server",
        )
        == "new-secret"
    )

    await service.delete(credential_id=credential_id)

    with pytest.raises(CredentialNotFound):
        await service.get_decrypted(credential_id=credential_id, requesting_kind="mcp_server")
