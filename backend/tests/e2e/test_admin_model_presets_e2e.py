"""E2E tests for /api/v1/admin/model-presets (GET / PUT).

Covers the admin CRUD round-trip on ``OrgSettings.model_presets`` with the
tiered ``ModelPresetsConfig`` shape: empty-org behaviour, write-then-read
fidelity, broken-ref rejection, and non-admin gating.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url
from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings

pytestmark = pytest.mark.e2e  # belt + suspenders alongside conftest auto-mark


async def _purge_system_presets_row() -> None:
    """Drop the system-level (org_id IS NULL) model_presets row.

    App lifespan re-seeds this row in the legacy ``{presets, chain}`` shape
    (the seeder migration is a separate task), which the new
    ``ModelPresetsConfig`` schema rejects at load time. Tests that read the
    snapshot / system fallback must start from a clean system row.
    """
    eng = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            await s.execute(
                delete(OrgSettings).where(
                    OrgSettings.org_id.is_(None),  # type: ignore[union-attr]
                    OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
                )
            )
            await s.commit()
    finally:
        await eng.dispose()


async def _create_provider_with_model(
    client: httpx.AsyncClient,
    *,
    provider_name: str,
    model_id: str,
) -> tuple[str, str]:
    """Create an org provider + one model on it; return (provider_slug, model_id)."""
    resp = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": provider_name,
            "provider_type": "openai-completions",
            "base_url": "https://example.com/v1",
            "auth_type": "api_key",
            "api_key": "sk-test",
        },
    )
    assert resp.status_code == 201, resp.text
    provider = resp.json()
    pid = provider["id"]
    slug = provider["slug"]

    resp = await client.post(
        f"/api/v1/admin/providers/{pid}/models",
        json={
            "model_id": model_id,
            "display_name": model_id,
            "context_window": 128_000,
            "max_tokens": 4096,
        },
    )
    assert resp.status_code == 201, resp.text
    return slug, model_id


def _disabled_tiers() -> dict[str, dict[str, Any]]:
    off = {"enabled": False, "primary": None, "fallbacks": []}
    return {t: dict(off) for t in ("lite", "flash", "max")}


def _config_with_pro(ref: str) -> dict[str, Any]:
    """A valid ModelPresetsConfig: only `pro` enabled, pointing at `ref`."""
    tiers = _disabled_tiers()
    tiers["pro"] = {"enabled": True, "primary": ref, "fallbacks": []}
    return {
        "tiers": tiers,
        "custom_presets": [],
        "default_preset": "pro",
        "task_routing": {"title": "pro"},
    }


async def test_get_when_no_row_returns_none(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """With no org row and the (legacy-shaped) system row purged, the admin
    endpoint reports origin='none' with a null value, 200."""
    client, _ = admin_client
    await _purge_system_presets_row()

    resp = await client.get("/api/v1/admin/model-presets")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["origin"] == "none", body
    assert body["value"] is None


async def test_put_then_get_round_trip(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """PUT a valid tiered body → GET reads back with origin='org' and same shape."""
    client, _ = admin_client
    await _purge_system_presets_row()

    slug, model_id = await _create_provider_with_model(
        client, provider_name="rt-provider", model_id="m1"
    )
    ref = f"{slug}/{model_id}"

    payload = _config_with_pro(ref)
    payload["custom_presets"] = [
        {"label": "alt", "primary": ref, "fallbacks": [], "description": "alternate"}
    ]

    resp = await client.put("/api/v1/admin/model-presets", json=payload)
    assert resp.status_code == 200, resp.text
    put_body = resp.json()
    assert put_body["origin"] == "org"
    assert put_body["value"]["tiers"]["pro"]["primary"] == ref
    assert put_body["value"]["default_preset"] == "pro"
    assert put_body["value"]["custom_presets"][0]["label"] == "alt"
    assert put_body["value"]["task_routing"] == {"title": "pro"}

    resp = await client.get("/api/v1/admin/model-presets")
    assert resp.status_code == 200, resp.text
    get_body = resp.json()
    assert get_body["origin"] == "org"
    assert get_body["value"]["tiers"]["pro"]["primary"] == ref
    assert get_body["value"]["custom_presets"][0]["label"] == "alt"


async def test_put_broken_ref_returns_400(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """PUT with pro.primary pointing at a non-existent model → 400 broken_preset."""
    client, _ = admin_client
    await _purge_system_presets_row()

    payload = _config_with_pro("ghost-provider/ghost-model")

    resp = await client.put("/api/v1/admin/model-presets", json=payload)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body.get("error_code") == "broken_preset", body


async def test_put_updates_existing_row(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Second PUT overwrites the first; GET returns the latest value."""
    client, _ = admin_client
    await _purge_system_presets_row()

    slug, _ = await _create_provider_with_model(
        client, provider_name="update-provider", model_id="m1"
    )
    slug2, _ = await _create_provider_with_model(
        client, provider_name="update-provider-2", model_id="m2"
    )
    ref1 = f"{slug}/m1"
    ref2 = f"{slug2}/m2"

    resp = await client.put("/api/v1/admin/model-presets", json=_config_with_pro(ref1))
    assert resp.status_code == 200, resp.text

    resp = await client.put("/api/v1/admin/model-presets", json=_config_with_pro(ref2))
    assert resp.status_code == 200, resp.text

    resp = await client.get("/api/v1/admin/model-presets")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["origin"] == "org"
    assert body["value"]["tiers"]["pro"]["primary"] == ref2


async def test_non_admin_member_forbidden(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """A workspace MEMBER (not admin) cannot read or write the org row."""
    client, _ = member_client

    resp = await client.get("/api/v1/admin/model-presets")
    assert resp.status_code == 403, resp.text

    resp = await client.put(
        "/api/v1/admin/model-presets",
        json=_config_with_pro("a/b"),
    )
    assert resp.status_code == 403, resp.text
