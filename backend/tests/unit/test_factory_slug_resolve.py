"""Tests for slug-based provider resolution in LLMFactory (Task 6).

Step 1: parse-contract unit test (passes before and after the keying change).
Step 1b: DB-backed resolution test — FAILS before Step 3-4 (name-keyed merged
map) and PASSES after (slug-keyed merged map).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubebox.llm.config import LLMConfig
from cubebox.llm.factory import LLMFactory

# ---------------------------------------------------------------------------
# Step 1: Parse-contract test (slug/model-id format; logic unchanged)
# ---------------------------------------------------------------------------


def test_parse_model_ref_returns_slug_and_model() -> None:
    slug, model_id = LLMFactory._parse_model_ref("my-deepseek/deepseek-v4-pro")
    assert slug == "my-deepseek"
    assert model_id == "deepseek-v4-pro"


# ---------------------------------------------------------------------------
# Step 1b: DB-backed resolution — fails before slug keying, passes after
# ---------------------------------------------------------------------------


@pytest.fixture()
async def sqlite_session():
    """In-memory SQLite session for fast DB-backed tests."""
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
async def test_resolve_default_provider_and_config_by_slug(
    sqlite_session: AsyncSession,
) -> None:
    """Resolver returns the right provider when default_model uses a slug ref.

    Seed: provider name='Routed Provider', slug='routed-provider', model 'm-1'.
    OrgSettings.default_model = 'routed-provider/m-1'.

    Before Task 6 (name-keyed merged map): FAILS — 'routed-provider' key absent.
    After Task 6 (slug-keyed merged map): PASSES.
    """
    from cubebox.models.org_settings import OrgSettings
    from cubebox.models.provider import Model as DBModel
    from cubebox.models.provider import Provider

    org_id = "org-slug-test"

    # Seed provider with distinct name vs slug
    prov = Provider(
        id="prov-slug-1",
        org_id=org_id,
        name="Routed Provider",
        slug="routed-provider",
        provider_type="openai-completions",
        base_url="https://route.test/v1",
        auth_type="none",
    )
    sqlite_session.add(prov)
    await sqlite_session.flush()

    # Seed model
    model = DBModel(
        id="mdl-slug-1",
        org_id=org_id,
        provider_id=prov.id,
        model_id="m-1",
        display_name="Model One",
        context_window=8000,
        max_tokens=1000,
        enabled=True,
    )
    sqlite_session.add(model)

    # Seed OrgSettings with slug-based ref
    setting = OrgSettings(
        org_id=org_id,
        key="default_model",
        value={"model_ref": "routed-provider/m-1"},
    )
    sqlite_session.add(setting)
    await sqlite_session.commit()

    factory = LLMFactory(
        llm_config=LLMConfig(
            default_model=None,  # no yaml fallback
            providers={},
        ),
        session=sqlite_session,
        org_id=org_id,
    )

    slug_result, model_id_result, _cfg = await factory.resolve_default_provider_and_config()
    assert slug_result == "routed-provider"
    assert model_id_result == "m-1"


@pytest.mark.asyncio
async def test_name_based_ref_does_not_resolve(
    sqlite_session: AsyncSession,
) -> None:
    """The old display-name ref 'Routed Provider/m-1' must NOT resolve after cutover."""
    from cubebox.models.org_settings import OrgSettings
    from cubebox.models.provider import Model as DBModel
    from cubebox.models.provider import Provider

    org_id = "org-name-test"

    prov = Provider(
        id="prov-name-1",
        org_id=org_id,
        name="Routed Provider",
        slug="routed-provider",
        provider_type="openai-completions",
        base_url="https://route.test/v1",
        auth_type="none",
    )
    sqlite_session.add(prov)
    await sqlite_session.flush()

    model = DBModel(
        id="mdl-name-1",
        org_id=org_id,
        provider_id=prov.id,
        model_id="m-1",
        display_name="Model One",
        context_window=8000,
        max_tokens=1000,
        enabled=True,
    )
    sqlite_session.add(model)

    # Use the OLD display-name ref — must fail after slug cutover
    setting = OrgSettings(
        org_id=org_id,
        key="default_model",
        value={"model_ref": "Routed Provider/m-1"},
    )
    sqlite_session.add(setting)
    await sqlite_session.commit()

    factory = LLMFactory(
        llm_config=LLMConfig(
            default_model=None,
            providers={},
        ),
        session=sqlite_session,
        org_id=org_id,
    )

    with pytest.raises(ValueError):
        await factory.resolve_default_provider_and_config()
