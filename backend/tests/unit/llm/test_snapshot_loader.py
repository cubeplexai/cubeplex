"""load_llm_snapshot — read DB providers + OrgSettings system row."""

from typing import Any

import pytest

from cubeplex.llm.errors import CorruptPresetsRowError
from cubeplex.llm.snapshot import ModelPreset, load_llm_snapshot
from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
from cubeplex.models.provider import Model, Provider


def _presets_value(primary: str = "acme/m1", default: str = "pro") -> dict[str, Any]:
    """A valid ModelPresetsConfig row with one enabled tier (pro)."""
    return {
        "tiers": {
            "lite": {"enabled": False, "primary": None},
            "flash": {"enabled": False, "primary": None},
            "pro": {"enabled": True, "primary": primary},
            "max": {"enabled": False, "primary": None},
        },
        "default_preset": default,
        "task_routing": {},
    }


def _add_acme_provider_and_model(async_session) -> None:
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
    return p


def _make_model(provider_id: str, model_id: str = "m1") -> Model:
    return Model(
        org_id=None,
        provider_id=provider_id,
        model_id=model_id,
        display_name=model_id,
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


@pytest.mark.asyncio
async def test_snapshot_loads_system_provider_and_preset(async_session, encryption_backend):
    p = _add_acme_provider_and_model(async_session)
    await async_session.flush()
    async_session.add(_make_model(p.id))
    async_session.add(OrgSettings(org_id=None, key=MODEL_PRESETS_KEY, value=_presets_value()))
    await async_session.commit()

    snap = await load_llm_snapshot(
        async_session, org_id="org_test", encryption_backend=encryption_backend
    )
    assert "acme" in snap.providers
    assert snap.model_presets == (
        ModelPreset(
            key="pro",
            primary="acme/m1",
            fallbacks=(),
            kind="tier",
            is_default=True,
            description="",
        ),
    )


@pytest.mark.asyncio
async def test_snapshot_loads_preset_with_partial_tiers(async_session, encryption_backend):
    """A tiers dict that only lists `pro` (no lite/flash/max keys at all) loads
    the same as listing them explicitly-disabled — missing tiers default to
    disabled in _load_presets, they don't raise KeyError."""
    p = _add_acme_provider_and_model(async_session)
    await async_session.flush()
    async_session.add(_make_model(p.id))
    async_session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value={
                "tiers": {"pro": {"enabled": True, "primary": "acme/m1"}},
                "default_preset": "pro",
                "task_routing": {},
            },
        )
    )
    await async_session.commit()

    snap = await load_llm_snapshot(
        async_session, org_id="org_test", encryption_backend=encryption_backend
    )
    assert snap.model_presets == (
        ModelPreset(
            key="pro",
            primary="acme/m1",
            fallbacks=(),
            kind="tier",
            is_default=True,
            description="",
        ),
    )


@pytest.mark.asyncio
async def test_org_row_replaces_system_row(async_session, encryption_backend):
    async_session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value=_presets_value(default="pro"),
        )
    )
    # Org row enables a custom preset labelled "org" as the default.
    org_value = _presets_value(default="org")
    org_value["custom_presets"] = [{"label": "org", "primary": "acme/m1"}]
    async_session.add(OrgSettings(org_id="org_test", key=MODEL_PRESETS_KEY, value=org_value))
    p = _add_acme_provider_and_model(async_session)
    await async_session.flush()
    async_session.add(_make_model(p.id))
    await async_session.commit()

    snap = await load_llm_snapshot(
        async_session, org_id="org_test", encryption_backend=encryption_backend
    )
    assert [pr.key for pr in snap.model_presets if pr.is_default] == ["org"]


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
            async_session.add(_make_model(prov.id, model_id=f"m{j}"))
    async_session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value=_presets_value(primary="prov0/m0"),
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
    # default_preset references an unavailable preset → schema rejects.
    bad_value = _presets_value(default="ghost")
    async_session.add(OrgSettings(org_id=None, key=MODEL_PRESETS_KEY, value=bad_value))
    await async_session.commit()
    with pytest.raises(CorruptPresetsRowError) as exc:
        await load_llm_snapshot(async_session, "org_test", encryption_backend)
    assert exc.value.error_code == "corrupt_presets_row"
    assert exc.value.status_code == 500
    assert len(exc.value.errors) >= 1
