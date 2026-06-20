"""E2E tests for /api/v1/ws/{workspace_id}/model-presets.

Workspace-side listing endpoint: returns the effective preset summaries
(key / kind / primary / description / is_default) — org row if present,
else system row, else empty. Fallback refs are NOT exposed.
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

    App lifespan seeds a valid tiered system row from ``config.test.yaml``.
    These tests assert ``origin='none'`` / ``origin='org'`` semantics, so they
    must start with NO system fallback row present.
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


async def _seed_config(
    admin: httpx.AsyncClient,
    *,
    provider_name: str,
    model_id: str,
) -> str:
    """Create one provider+model, then PUT an org config: `pro` tier + a custom.

    Returns the chain ref ("slug/model_id").
    """
    resp = await admin.post(
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
    slug = resp.json()["slug"]
    pid = resp.json()["id"]

    resp = await admin.post(
        f"/api/v1/admin/providers/{pid}/models",
        json={
            "model_id": model_id,
            "display_name": model_id,
            "context_window": 128_000,
            "max_tokens": 4096,
        },
    )
    assert resp.status_code == 201, resp.text

    ref = f"{slug}/{model_id}"
    off = {"enabled": False, "primary": None, "fallbacks": []}
    payload: dict[str, Any] = {
        "tiers": {
            "lite": dict(off),
            "flash": dict(off),
            "pro": {"enabled": True, "primary": ref, "fallbacks": []},
            "max": dict(off),
        },
        "custom_presets": [
            {"label": "fancy", "primary": ref, "fallbacks": [], "description": "a custom one"}
        ],
        "default_preset": "pro",
        "task_routing": {},
    }
    resp = await admin.put("/api/v1/admin/model-presets", json=payload)
    assert resp.status_code == 200, resp.text
    return ref


async def test_member_sees_effective_preset_summaries(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Admin (also a member) GETs the workspace listing after seeding presets."""
    client, ws_id = admin_client
    await _purge_system_presets_row()
    ref = await _seed_config(client, provider_name="ws-list-provider", model_id="m1")

    resp = await client.get(f"/api/v1/ws/{ws_id}/model-presets")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "presets" in body
    by_key = {p["key"]: p for p in body["presets"]}

    assert "pro" in by_key, body
    pro = by_key["pro"]
    assert pro["kind"] == "tier"
    assert pro["primary"] == ref
    assert pro["is_default"] is True
    # Tiers carry no stored description; frontend supplies i18n copy by key.
    assert pro["description"] == ""

    assert "fancy" in by_key, body
    fancy = by_key["fancy"]
    assert fancy["kind"] == "custom"
    assert fancy["primary"] == ref
    assert fancy["is_default"] is False
    assert fancy["description"] == "a custom one"


async def test_non_member_of_workspace_403(
    member_client_org_a: tuple[httpx.AsyncClient, str],
    member_client_org_b: tuple[httpx.AsyncClient, str],
) -> None:
    """A user from a different org / workspace gets 403 on someone else's ws.

    `require_member` returns 403 (not 404) when the workspace exists but
    the caller has no membership row for it.
    """
    _, ws_a = member_client_org_a
    client_b, _ = member_client_org_b

    resp = await client_b.get(f"/api/v1/ws/{ws_a}/model-presets")
    assert resp.status_code == 403, resp.text


async def test_member_sees_well_formed_list_when_no_presets(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Fresh member workspace with no admin-written presets returns a
    well-formed list (or whatever the system fallback provides) without 5xx-ing."""
    client, ws_id = member_client
    await _purge_system_presets_row()

    resp = await client.get(f"/api/v1/ws/{ws_id}/model-presets")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["presets"], list)
    for p in body["presets"]:
        assert p["kind"] in ("tier", "custom")
        assert "key" in p
        assert "primary" in p
        assert "is_default" in p
