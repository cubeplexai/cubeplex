"""load_llm_snapshot — read DB providers + OrgSettings system row."""

import pytest

from cubebox.llm.errors import CorruptPresetsRowError
from cubebox.llm.snapshot import LLMPreset, load_llm_snapshot
from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
from cubebox.models.provider import Model, Provider


@pytest.mark.asyncio
async def test_snapshot_loads_system_provider_and_preset(async_session, encryption_backend):
    # Seed a system provider + model.
    p = Provider(
        org_id=None,
        name="acme",
        slug="acme",
        provider_type="openai-completions",
        base_url="https://x",
        auth_type="api_key",
        enabled=True,
    )
    async_session.add(p)
    await async_session.flush()
    async_session.add(
        Model(
            org_id=None,
            provider_id=p.id,
            model_id="m1",
            display_name="m1",
            reasoning=False,
            input_modalities=["text"],
            cost_input=0,
            cost_output=0,
            cost_cache_read=0,
            cost_cache_write=0,
            context_window=128000,
            max_tokens=32000,
            enabled=True,
        )
    )
    # Seed system model_presets row.
    async_session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value={
                "presets": [{"label": "default", "chain": ["acme/m1"], "is_default": True}],
                "task_presets": {},
            },
        )
    )
    await async_session.commit()

    snap = await load_llm_snapshot(
        async_session, org_id="org_test", encryption_backend=encryption_backend
    )
    assert "acme" in snap.providers
    assert snap.presets == (LLMPreset(label="default", chain=("acme/m1",), is_default=True),)


@pytest.mark.asyncio
async def test_org_row_replaces_system_row(async_session, encryption_backend):
    async_session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value={
                "presets": [{"label": "sys", "chain": ["acme/m1"], "is_default": True}],
                "task_presets": {},
            },
        )
    )
    async_session.add(
        OrgSettings(
            org_id="org_test",
            key=MODEL_PRESETS_KEY,
            value={
                "presets": [{"label": "org", "chain": ["acme/m1"], "is_default": True}],
                "task_presets": {},
            },
        )
    )
    # Seed provider/model so refs validate.
    p = Provider(
        org_id=None,
        name="acme",
        slug="acme",
        provider_type="openai-completions",
        base_url="https://x",
        auth_type="api_key",
        enabled=True,
    )
    async_session.add(p)
    await async_session.flush()
    async_session.add(
        Model(
            org_id=None,
            provider_id=p.id,
            model_id="m1",
            display_name="m1",
            reasoning=False,
            input_modalities=["text"],
            cost_input=0,
            cost_output=0,
            cost_cache_read=0,
            cost_cache_write=0,
            context_window=128000,
            max_tokens=32000,
            enabled=True,
        )
    )
    await async_session.commit()

    snap = await load_llm_snapshot(
        async_session, org_id="org_test", encryption_backend=encryption_backend
    )
    assert [p.label for p in snap.presets] == ["org"]


@pytest.mark.asyncio
async def test_malformed_row_raises_corrupt_presets_row_error(async_session, encryption_backend):
    # Two is_default=true → schema rejects.
    async_session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value={
                "presets": [
                    {"label": "a", "chain": ["x/y"], "is_default": True},
                    {"label": "b", "chain": ["x/z"], "is_default": True},
                ],
                "task_presets": {},
            },
        )
    )
    await async_session.commit()
    with pytest.raises(CorruptPresetsRowError) as exc:
        await load_llm_snapshot(async_session, "org_test", encryption_backend)
    assert exc.value.error_code == "corrupt_presets_row"
    assert exc.value.status_code == 500
    assert len(exc.value.errors) >= 1
