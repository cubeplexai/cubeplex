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
async def test_load_providers_is_not_n_plus_one(async_session, encryption_backend):
    """_load_providers must batch models + credentials regardless of provider count.

    Counts SELECTs against `models` and `credentials` and asserts they stay at
    a constant (== 1 each, plus 0 when the per-table input list is empty),
    not O(P) and O(C) as the original implementation did.
    """
    from sqlalchemy import event

    # Seed 5 system providers, each with 2 enabled models. No credentials yet —
    # api_key path is independently exercised in other tests.
    for i in range(5):
        prov = Provider(
            org_id=None,
            name=f"prov{i}",
            slug=f"prov{i}",
            provider_type="openai-completions",
            base_url="https://x",
            auth_type="api_key",
            enabled=True,
        )
        async_session.add(prov)
        await async_session.flush()
        for j in range(2):
            async_session.add(
                Model(
                    org_id=None,
                    provider_id=prov.id,
                    model_id=f"m{j}",
                    display_name=f"m{j}",
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
    async_session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value={
                "presets": [{"label": "d", "chain": ["prov0/m0"], "is_default": True}],
                "task_presets": {},
            },
        )
    )
    await async_session.commit()

    counts: dict[str, int] = {"models": 0, "credentials": 0}

    sync_engine = async_session.bind.sync_engine  # type: ignore[union-attr]

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        low = statement.lower()
        if "from models" in low:
            counts["models"] += 1
        if "from credentials" in low:
            counts["credentials"] += 1

    event.listen(sync_engine, "before_cursor_execute", _before_cursor_execute)
    try:
        await load_llm_snapshot(
            async_session, org_id="org_test", encryption_backend=encryption_backend
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", _before_cursor_execute)

    # One batched SELECT against models (covers all 5 providers).
    assert counts["models"] == 1, f"expected single batched models query, got {counts['models']}"
    # No credentials referenced → zero queries (empty cred_ids skips the IN).
    assert counts["credentials"] == 0, (
        f"expected no credentials query when cred_ids empty, got {counts['credentials']}"
    )


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
