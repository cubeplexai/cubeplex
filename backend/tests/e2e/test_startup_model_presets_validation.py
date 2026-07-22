"""Lifespan fails fast on an invalid llm.model_presets config.

Regression test: seed_model_presets_from_config's ModelPresetsConfig validation
error used to be caught by the same warn-and-continue try/except as system
provider seeding in app.py's lifespan. The app booted healthy with zero presets
seeded, and every chat message then failed at runtime with no_default_preset
instead of the deployment failing at startup where the bad config actually is.

Also covers the follow-up relaxation: `tiers` only needs at least one entry
(missing tiers default to disabled downstream), not all four by name.
"""

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.api.app import create_app, lifespan
from cubeplex.db.engine import _build_database_url
from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings

pytestmark = pytest.mark.e2e


async def _wipe_system_model_presets() -> None:
    """seed_model_presets_from_config is skip-if-exists; clear the system row
    first so an invalid config actually reaches ModelPresetsConfig validation
    instead of short-circuiting on "row present — preserving"."""
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await session.execute(
                delete(OrgSettings).where(OrgSettings.key == MODEL_PRESETS_KEY)  # type: ignore[arg-type]
            )
            await session.commit()
    finally:
        await test_engine.dispose()


async def test_startup_aborts_on_empty_model_presets_tiers(monkeypatch):
    """An empty tiers map (config declares model_presets but lists no tiers at
    all) must abort startup, not warn. A *partial* tiers map (e.g. only
    `flash`) is valid — see test_model_presets_schema.py::test_partial_tiers_accepted
    and test_snapshot_loader.py::test_snapshot_loads_preset_with_partial_tiers —
    the invariant this guards is "at least one tier", not "all four"."""
    await _wipe_system_model_presets()
    monkeypatch.setattr(
        "cubeplex.seeders.provider_seeder.settings",
        {
            "llm": {
                "model_presets": {
                    "tiers": {},
                    "default_preset": "flash",
                }
            }
        },
    )

    app = create_app()
    with pytest.raises(Exception, match="at least one tier"):
        async with lifespan(app):
            pass


async def test_startup_succeeds_with_partial_model_presets_tiers(monkeypatch):
    """Only `flash` defined (lite/pro/max omitted entirely) boots cleanly."""
    await _wipe_system_model_presets()
    monkeypatch.setattr(
        "cubeplex.seeders.provider_seeder.settings",
        {
            "llm": {
                "model_presets": {
                    "tiers": {
                        "flash": {
                            "enabled": True,
                            "primary": "acme/m1",
                            "fallbacks": [],
                        },
                    },
                    "default_preset": "flash",
                }
            }
        },
    )

    app = create_app()
    async with lifespan(app):
        pass
