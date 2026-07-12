"""ProviderService invariant tests -- scope/name validation, system protection."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubeplex.api.schemas.provider import ProviderCreate, ProviderUpdate
from cubeplex.credentials.encryption import FernetBackend
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.services.credential import CredentialService
from cubeplex.services.provider_service import (
    ProviderNameConflictError,
    ProviderOAuthNotImplementedError,
    ProviderService,
    ProviderSystemReadonlyError,
)


@pytest.fixture()
async def db_session():
    """In-memory SQLite session for fast unit tests."""
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


def _make_svc(session: AsyncSession, org_id: str = "org-1") -> ProviderService:
    from cubeplex.repositories.model import ModelRepository
    from cubeplex.repositories.org_provider_override import OrgProviderOverrideRepository
    from cubeplex.repositories.provider import ProviderRepository

    backend = FernetBackend([Fernet.generate_key()])
    cred_service = CredentialService(
        CredentialRepository(session, org_id=org_id),
        backend,
        org_id=org_id,
        actor_user_id="user-1",
    )
    return ProviderService(
        provider_repo=ProviderRepository(session, org_id=org_id),
        model_repo=ModelRepository(session),
        override_repo=OrgProviderOverrideRepository(session, org_id=org_id),
        credential_service=cred_service,
        session=session,
        org_id=org_id,
        actor_user_id="user-1",
    )


async def test_oauth_auth_type_rejected(db_session: AsyncSession) -> None:
    """auth_type=oauth v1 must raise ProviderOAuthNotImplementedError."""
    svc = _make_svc(db_session)
    data = ProviderCreate(
        name="test-oauth",
        base_url="https://example.com/api",
        auth_type="oauth",
    )
    with pytest.raises(ProviderOAuthNotImplementedError):
        await svc.create_provider(data)


async def test_create_org_provider_sets_org_id(db_session: AsyncSession) -> None:
    """Org-level provider must have org_id set and credential_id populated."""
    svc = _make_svc(db_session)
    data = ProviderCreate(
        name="my-provider",
        base_url="https://example.com/api",
        auth_type="api_key",
        api_key="sk-test",
    )
    provider = await svc.create_provider(data)
    assert provider.org_id == "org-1"
    assert provider.name == "my-provider"
    assert provider.credential_id is not None


async def test_name_conflict_raises(db_session: AsyncSession) -> None:
    """Duplicate name in same scope must raise ProviderNameConflictError."""
    svc = _make_svc(db_session)
    data = ProviderCreate(
        name="dup-provider",
        base_url="https://example.com/api",
        auth_type="api_key",
        api_key="sk-dup",
    )
    await svc.create_provider(data)
    with pytest.raises(ProviderNameConflictError):
        await svc.create_provider(data)


async def test_system_provider_readonly_on_update(db_session: AsyncSession) -> None:
    """Updating a system provider (org_id=None) must raise."""
    from cubeplex.models.provider import Provider

    p = Provider(
        org_id=None,
        name="system-openai",
        slug="system-openai",
        base_url="https://api.openai.com",
        auth_type="api_key",
        created_by_user_id="system",
    )
    db_session.add(p)
    await db_session.commit()

    svc = _make_svc(db_session)
    with pytest.raises(ProviderSystemReadonlyError):
        await svc.update_provider(p.id, ProviderUpdate(name="renamed"))


async def test_system_provider_readonly_on_delete(db_session: AsyncSession) -> None:
    """Deleting a system provider (org_id=None) must raise."""
    from cubeplex.models.provider import Provider

    p = Provider(
        org_id=None,
        name="system-anthropic",
        slug="system-anthropic",
        base_url="https://api.anthropic.com",
        auth_type="api_key",
        created_by_user_id="system",
    )
    db_session.add(p)
    await db_session.commit()

    svc = _make_svc(db_session)
    with pytest.raises(ProviderSystemReadonlyError):
        await svc.delete_provider(p.id)


async def test_auth_type_none_rejects_api_key(db_session: AsyncSession) -> None:
    """auth_type=none must reject api_key being set."""
    svc = _make_svc(db_session)
    data = ProviderCreate(
        name="no-key-plz",
        base_url="https://example.com/api",
        auth_type="none",
        api_key="should-not-be-here",
    )
    with pytest.raises(ValueError, match="api_key must be empty"):
        await svc.create_provider(data)
