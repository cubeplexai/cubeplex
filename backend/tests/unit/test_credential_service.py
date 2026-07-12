"""Tests for CredentialService vault behavior."""

from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.credentials.exceptions import CredentialKindMismatch, CredentialNotFound
from cubeplex.repositories.credential import CredentialRepository


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
    from cubeplex.services.credential import CredentialService

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
    from cubeplex.services.credential import CredentialService

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
    from cubeplex.services.credential import CredentialService

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


async def test_credential_service_upsert_by_kind_name_rotates_existing(
    session: AsyncSession, backend: FernetBackend
) -> None:
    """Re-OAuth must rotate the existing (org, kind, name) row in place
    rather than insert a duplicate that violates ``uq_credential_org_kind_name``.

    Regression for the bug where ``OAuthCallbackHandler._persist_org``
    blindly called ``create`` on every callback, so re-authorizing an
    install whose tokens were already in the vault crashed with a
    psycopg ``UniqueViolation``.
    """
    from cubeplex.services.credential import CredentialService

    service = CredentialService(
        CredentialRepository(session, org_id="org-1"),
        backend,
        org_id="org-1",
        actor_user_id="user-1",
    )

    first_id = await service.upsert_by_kind_name(
        kind="mcp_oauth_access_token",
        name="mcp:catalog:notion:org:access",
        plaintext="token-v1",
    )
    second_id = await service.upsert_by_kind_name(
        kind="mcp_oauth_access_token",
        name="mcp:catalog:notion:org:access",
        plaintext="token-v2",
    )

    assert first_id == second_id, "upsert must rotate the same row, not insert a duplicate"
    assert (
        await service.get_decrypted(
            credential_id=first_id,
            requesting_kind="mcp_oauth_access_token",
        )
        == "token-v2"
    )

    # A different (kind, name) tuple is a separate row.
    other_id = await service.upsert_by_kind_name(
        kind="mcp_oauth_refresh_token",
        name="mcp:catalog:notion:org:refresh",
        plaintext="refresh-v1",
    )
    assert other_id != first_id
