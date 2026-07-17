"""Seed idempotency tests."""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.models.provider import Model, Provider
from cubeplex.seeders import seed_system_providers_from_config


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
        name="cubeplex",
        slug="cubeplex",
        provider_type="openai-completions",
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
        await clean_db.execute(select(Provider).where(Provider.name == "cubeplex"))
    ).scalar_one()
    assert updated.base_url != "http://old-url"
    assert updated.provider_type == "openai-completions"


async def test_seed_backfills_capability_for_preset_ref(
    clean_db: AsyncSession,
    backend: FernetBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider with a ``preset:`` ref gets its capability snapshot + preset_key.

    Under the new rule (spec §6.2), backfill is driven by an explicit ``preset:``
    reference into the cubeplex catalog — not a name==slug match. A custom provider
    (no preset) is left with no preset_slug and no capability.
    """
    fake_llm = {
        "providers": {
            "ds": {
                "preset": "deepseek/cn/openai-completions",
                "api_key": "k",
            },
            "house-brand": {
                "base_url": "http://localhost:9000/v1",
                "api": "openai-completions",
                "models": [{"id": "house", "name": "House"}],
            },
        }
    }
    monkeypatch.setattr(
        "cubeplex.seeders.provider_seeder.settings",
        {"llm": fake_llm},
        raising=True,
    )

    await seed_system_providers_from_config(clean_db, backend)

    ds = (
        await clean_db.execute(select(Provider).where(Provider.name == "ds"))
    ).scalar_one_or_none()
    assert ds is not None, "expected a seeded 'ds' provider"
    assert ds.preset_slug == "deepseek/cn/openai-completions"
    assert ds.capability, "capability snapshot must be populated for a preset ref"
    assert ds.base_url == "https://api.deepseek.com"

    # No preset -> custom provider, capability stays empty.
    house = (
        await clean_db.execute(select(Provider).where(Provider.name == "house-brand"))
    ).scalar_one()
    assert house.preset_slug is None
    assert not house.capability


async def test_seed_refreshes_legacy_seeded_capability(
    clean_db: AsyncSession,
    backend: FernetBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A previously seeded system provider with legacy capability keys is upgraded."""
    provider = Provider(
        org_id=None,
        name="ds",
        slug="ds",
        provider_type="openai-completions",
        base_url="https://old.example/v1",
        auth_type="api_key",
        enabled=True,
        created_by_user_id=None,
        preset_slug="deepseek/cn/openai-completions",
        capability={
            "reasoning_off_payload": {"extra_body": {"reasoning": {"exclude": True}}},
            "reasoning_on_payload": {"extra_body": {"reasoning": {"exclude": False}}},
        },
    )
    clean_db.add(provider)
    await clean_db.commit()

    fake_llm = {
        "providers": {
            "ds": {
                "preset": "deepseek/cn/openai-completions",
                "api_key": "k",
            },
        }
    }
    monkeypatch.setattr(
        "cubeplex.seeders.provider_seeder.settings",
        {"llm": fake_llm},
        raising=True,
    )

    await seed_system_providers_from_config(clean_db, backend)

    refreshed = (await clean_db.execute(select(Provider).where(Provider.name == "ds"))).scalar_one()
    assert "reasoning_off_payload" not in refreshed.capability
    assert refreshed.capability["reasoning"]["mode_payloads"]["off"] == {
        "extra_body": {"reasoning": {"exclude": True}}
    }


async def test_seed_dedups_colliding_slugs(
    clean_db: AsyncSession,
    backend: FernetBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two config names that slugify to the same value must not collide on insert.

    `Acme AI` and `acme-ai` both slugify to `acme-ai`; the seeder must suffix the
    second (`acme-ai-2`) instead of violating the uq_provider_system_slug index.
    """
    fake_llm = {
        "providers": {
            "Acme AI": {
                "base_url": "http://localhost:8000/v1",
                "api": "openai-completions",
                "models": [{"id": "a1", "name": "A1"}],
            },
            "acme-ai": {
                "base_url": "http://localhost:8001/v1",
                "api": "openai-completions",
                "models": [{"id": "a2", "name": "A2"}],
            },
        }
    }
    monkeypatch.setattr(
        "cubeplex.seeders.provider_seeder.settings", {"llm": fake_llm}, raising=True
    )

    await seed_system_providers_from_config(clean_db, backend)

    slugs = {p.name: p.slug for p in (await clean_db.execute(select(Provider))).scalars().all()}
    assert {slugs["Acme AI"], slugs["acme-ai"]} == {"acme-ai", "acme-ai-2"}
