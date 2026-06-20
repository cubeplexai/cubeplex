"""seed_model_presets_from_config writes the system row; idempotent after."""

import pytest
from sqlalchemy import select

from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
from cubebox.seeders.provider_seeder import seed_model_presets_from_config


def _tiered_llm_config() -> dict:
    """A valid llm.model_presets block: lite + pro enabled, default=pro."""
    return {
        "model_presets": {
            "tiers": {
                "lite": {"enabled": True, "primary": "acme/m1", "fallbacks": []},
                "flash": {"enabled": False, "primary": None, "fallbacks": []},
                "pro": {
                    "enabled": True,
                    "primary": "acme/m2",
                    "fallbacks": ["acme/m3"],
                },
                "max": {"enabled": False, "primary": None, "fallbacks": []},
            },
            "default_preset": "pro",
            "task_routing": {},
        }
    }


@pytest.mark.asyncio
async def test_first_run_writes_tiered_presets(async_session, monkeypatch):
    monkeypatch.setattr(
        "cubebox.seeders.provider_seeder.settings",
        {"llm": _tiered_llm_config()},
    )
    # Start from a clean system row.
    existing = (
        await async_session.execute(
            select(OrgSettings).where(
                OrgSettings.org_id.is_(None),
                OrgSettings.key == MODEL_PRESETS_KEY,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await async_session.delete(existing)
        await async_session.commit()

    await seed_model_presets_from_config(async_session)

    row = (
        await async_session.execute(
            select(OrgSettings).where(
                OrgSettings.org_id.is_(None),
                OrgSettings.key == MODEL_PRESETS_KEY,
            )
        )
    ).scalar_one()
    val = row.value
    assert val["default_preset"] == "pro"
    assert val["tiers"]["pro"]["primary"] == "acme/m2"
    assert val["tiers"]["pro"]["fallbacks"] == ["acme/m3"]


@pytest.mark.asyncio
async def test_second_run_does_not_overwrite_admin_edits(async_session, monkeypatch):
    monkeypatch.setattr(
        "cubebox.seeders.provider_seeder.settings",
        {"llm": _tiered_llm_config()},
    )
    # Pre-existing admin-edited system row: default points at the lite tier.
    admin_value = {
        "tiers": {
            "lite": {"enabled": True, "primary": "admin/m0", "fallbacks": []},
            "flash": {"enabled": False, "primary": None, "fallbacks": []},
            "pro": {"enabled": False, "primary": None, "fallbacks": []},
            "max": {"enabled": False, "primary": None, "fallbacks": []},
        },
        "custom_presets": [],
        "default_preset": "lite",
        "task_routing": {},
    }
    async_session.add(OrgSettings(org_id=None, key=MODEL_PRESETS_KEY, value=admin_value))
    await async_session.commit()

    await seed_model_presets_from_config(async_session)

    row = (
        await async_session.execute(
            select(OrgSettings).where(
                OrgSettings.org_id.is_(None),
                OrgSettings.key == MODEL_PRESETS_KEY,
            )
        )
    ).scalar_one()
    # Untouched: the admin row, not the config seed.
    assert row.value["default_preset"] == "lite"
    assert row.value["tiers"]["lite"]["primary"] == "admin/m0"
