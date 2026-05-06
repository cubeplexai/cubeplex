"""Seed idempotency tests."""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubebox.credentials.encryption import FernetBackend
from cubebox.models.provider import Model, Provider
from cubebox.seeders import seed_system_providers_from_config


@pytest.fixture()
async def clean_db() -> AsyncSession:
    """In-memory SQLite session with clean tables for seed tests."""
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


@pytest.fixture()
def backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


async def test_seed_is_idempotent(clean_db: AsyncSession, backend: FernetBackend) -> None:
    """Seeding twice must produce the same set of system providers and models."""
    await seed_system_providers_from_config(clean_db, backend)
    providers1 = (
        (await clean_db.execute(select(Provider).where(Provider.org_id.is_(None)))).scalars().all()
    )

    await seed_system_providers_from_config(clean_db, backend)
    providers2 = (
        (await clean_db.execute(select(Provider).where(Provider.org_id.is_(None)))).scalars().all()
    )

    assert len(providers1) == len(providers2)

    for p in providers1:
        models = (
            (await clean_db.execute(select(Model).where(Model.provider_id == p.id))).scalars().all()
        )
        assert len(models) > 0, f"Provider {p.name} should have models after seed"


async def test_seed_creates_providers(clean_db: AsyncSession, backend: FernetBackend) -> None:
    """Seeding must create system providers with their models."""
    await seed_system_providers_from_config(clean_db, backend)
    providers = (
        (await clean_db.execute(select(Provider).where(Provider.org_id.is_(None)))).scalars().all()
    )
    # There should be at least one system provider seeded
    assert len(providers) > 0

    for p in providers:
        assert p.org_id is None
        assert p.created_by_user_id is None
        models = (
            (await clean_db.execute(select(Model).where(Model.provider_id == p.id))).scalars().all()
        )
        assert len(models) > 0, f"Provider {p.name} should have models"


async def test_seed_updates_existing_provider_url(
    clean_db: AsyncSession, backend: FernetBackend
) -> None:
    """Seeding an existing provider must update its base_url."""
    # Insert a provider manually with a different URL
    p = Provider(
        org_id=None,
        name="cubebox",
        provider_type="openai_compat",
        base_url="http://old-url",
        auth_type="api_key",
        enabled=True,
        created_by_user_id=None,
    )
    clean_db.add(p)
    await clean_db.commit()

    await seed_system_providers_from_config(clean_db, backend)

    # Verify the URL was updated
    updated = (
        await clean_db.execute(select(Provider).where(Provider.name == "cubebox"))
    ).scalar_one()
    assert updated.base_url != "http://old-url"
    assert updated.provider_type == "openai_compat"
