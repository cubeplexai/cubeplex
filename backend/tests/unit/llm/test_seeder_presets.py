"""Seeder writes OrgSettings.model_presets on first run; idempotent after."""

import pytest
from sqlalchemy import select

from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
from cubebox.seeders.provider_seeder import seed_default_presets_from_config


@pytest.mark.asyncio
async def test_first_run_writes_default_preset(async_session, monkeypatch):
    monkeypatch.setattr(
        "cubebox.config.config.llm",
        {
            "default_model": "acme/m1",
            "fallback_models": ["acme/m2"],
            "title_model": "acme/mini",
            "compaction": {"summary_model": "acme/mini"},
        },
    )
    await seed_default_presets_from_config(async_session)
    await async_session.commit()
    row = (
        await async_session.execute(
            select(OrgSettings).where(
                OrgSettings.org_id.is_(None),
                OrgSettings.key == MODEL_PRESETS_KEY,
            )
        )
    ).scalar_one()
    val = row.value
    labels = {p["label"] for p in val["presets"]}
    assert "default" in labels
    default = next(p for p in val["presets"] if p["label"] == "default")
    assert default["chain"] == ["acme/m1", "acme/m2"]
    assert default["is_default"] is True
    # task_presets entries created for distinct task models.
    assert val["task_presets"].get("title") in labels
    assert val["task_presets"].get("compaction") in labels


@pytest.mark.asyncio
async def test_second_run_does_not_overwrite_admin_edits(async_session, monkeypatch):
    monkeypatch.setattr(
        "cubebox.config.config.llm",
        {
            "default_model": "acme/m1",
            "fallback_models": [],
        },
    )
    async_session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value={
                "presets": [{"label": "custom", "chain": ["acme/m1"], "is_default": True}],
                "task_presets": {},
            },
        )
    )
    await async_session.commit()
    await seed_default_presets_from_config(async_session)
    await async_session.commit()
    row = (
        await async_session.execute(
            select(OrgSettings).where(
                OrgSettings.org_id.is_(None),
                OrgSettings.key == MODEL_PRESETS_KEY,
            )
        )
    ).scalar_one()
    labels = {p["label"] for p in row.value["presets"]}
    assert labels == {"custom"}  # not overwritten
