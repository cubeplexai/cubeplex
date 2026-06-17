"""Tests for the ExternalIdentityRepository."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.models.external_identity import ExternalIdentity
from cubebox.repositories.external_identity import ExternalIdentityRepository


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _make_identity(
    *,
    user_id: str = "usr-1",
    provider_type: str = "oidc_sso",
    provider_id: str = "sso-acme",
    external_id: str = "ext-123",
    external_email: str = "user@example.com",
) -> ExternalIdentity:
    return ExternalIdentity(
        user_id=user_id,
        provider_type=provider_type,
        provider_id=provider_id,
        external_id=external_id,
        external_email=external_email,
    )


async def test_add_returns_persisted_identity(session: AsyncSession) -> None:
    repo = ExternalIdentityRepository(session)
    saved = await repo.add(_make_identity())
    assert saved.id.startswith("eid-")
    assert saved.external_email == "user@example.com"


async def test_find_by_external_matches_exact_triple(session: AsyncSession) -> None:
    repo = ExternalIdentityRepository(session)
    saved = await repo.add(_make_identity())

    found = await repo.find_by_external(
        provider_type="oidc_sso",
        provider_id="sso-acme",
        external_id="ext-123",
    )
    assert found is not None
    assert found.id == saved.id


async def test_find_by_external_distinguishes_provider_type(session: AsyncSession) -> None:
    """An OIDC sub and a SAML NameID with the same string value must not collide."""
    repo = ExternalIdentityRepository(session)
    await repo.add(_make_identity(provider_type="oidc_sso", external_id="shared-id"))
    await repo.add(
        _make_identity(
            user_id="usr-2",
            provider_type="saml_sso",
            external_id="shared-id",
            external_email="other@example.com",
        )
    )

    oidc = await repo.find_by_external(
        provider_type="oidc_sso", provider_id="sso-acme", external_id="shared-id"
    )
    saml = await repo.find_by_external(
        provider_type="saml_sso", provider_id="sso-acme", external_id="shared-id"
    )
    assert oidc is not None and saml is not None
    assert oidc.id != saml.id


async def test_find_by_external_returns_none_when_missing(session: AsyncSession) -> None:
    repo = ExternalIdentityRepository(session)
    assert (
        await repo.find_by_external(
            provider_type="google",
            provider_id="google",
            external_id="missing",
        )
        is None
    )


async def test_list_by_user_returns_all_links(session: AsyncSession) -> None:
    repo = ExternalIdentityRepository(session)
    await repo.add(_make_identity(provider_type="oidc_sso", external_id="oidc-1"))
    await repo.add(
        _make_identity(
            provider_type="google",
            provider_id="google",
            external_id="g-1",
            external_email="user@example.com",
        )
    )
    await repo.add(
        _make_identity(
            user_id="usr-other",
            provider_type="oidc_sso",
            external_id="other-oidc",
            external_email="other@example.com",
        )
    )

    rows = await repo.list_by_user("usr-1")
    assert {r.provider_type for r in rows} == {"oidc_sso", "google"}
    assert len(rows) == 2


async def test_list_by_connection_returns_all_org_users(session: AsyncSession) -> None:
    repo = ExternalIdentityRepository(session)
    await repo.add(_make_identity(provider_id="sso-acme", external_id="a"))
    await repo.add(
        _make_identity(
            user_id="usr-2",
            provider_id="sso-acme",
            external_id="b",
            external_email="b@example.com",
        )
    )
    await repo.add(
        _make_identity(
            user_id="usr-3",
            provider_id="sso-other",
            external_id="c",
            external_email="c@example.com",
        )
    )

    rows = await repo.list_by_connection("sso-acme")
    assert {r.external_id for r in rows} == {"a", "b"}


async def test_delete_returns_true_when_removed(session: AsyncSession) -> None:
    repo = ExternalIdentityRepository(session)
    saved = await repo.add(_make_identity())

    deleted = await repo.delete(saved.id)
    assert deleted is True
    assert (
        await repo.find_by_external(
            provider_type="oidc_sso", provider_id="sso-acme", external_id="ext-123"
        )
        is None
    )


async def test_delete_missing_id_returns_false(session: AsyncSession) -> None:
    repo = ExternalIdentityRepository(session)
    assert await repo.delete("eid-does-not-exist") is False
