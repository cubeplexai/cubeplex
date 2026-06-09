"""E2E tests for /api/v1/admin/model-presets (GET / PUT).

Covers the admin CRUD round-trip on `OrgSettings.model_presets`: empty-org
behaviour, write-then-read fidelity, broken-ref rejection, and non-admin
gating.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.e2e  # belt + suspenders alongside conftest auto-mark


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


async def test_get_when_no_row_returns_none(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """A freshly-bootstrapped org with no admin-written presets has
    origin='none' OR origin='system' (if a system-level row was seeded
    by app bootstrap). Either way, the endpoint must respond 200 with a
    well-formed body."""
    client, _ = admin_client

    resp = await client.get("/api/v1/admin/model-presets")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["origin"] in ("none", "system", "org")
    if body["origin"] == "none":
        assert body["value"] is None
    else:
        assert body["value"] is not None
        assert isinstance(body["value"]["presets"], list)


async def test_put_then_get_round_trip(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """PUT a valid body → GET reads back with origin='org' and same shape."""
    client, _ = admin_client

    slug, model_id = await _create_provider_with_model(
        client, provider_name="rt-provider", model_id="m1"
    )
    ref = f"{slug}/{model_id}"

    payload: dict[str, Any] = {
        "presets": [
            {"label": "default", "chain": [ref], "is_default": True},
            {"label": "alt", "chain": [ref], "is_default": False},
        ],
        "task_presets": {"title": "default"},
    }

    resp = await client.put("/api/v1/admin/model-presets", json=payload)
    assert resp.status_code == 200, resp.text
    put_body = resp.json()
    assert put_body["origin"] == "org"
    assert put_body["value"]["presets"][0]["label"] == "default"
    assert put_body["value"]["presets"][0]["chain"] == [ref]
    assert put_body["value"]["task_presets"] == {"title": "default"}

    resp = await client.get("/api/v1/admin/model-presets")
    assert resp.status_code == 200, resp.text
    get_body = resp.json()
    assert get_body["origin"] == "org"
    assert get_body["value"]["presets"][0]["label"] == "default"
    assert get_body["value"]["presets"][1]["label"] == "alt"
    assert get_body["value"]["task_presets"] == {"title": "default"}


async def test_put_broken_ref_returns_400(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """PUT with a chain ref pointing at a non-existent model → 400 broken_preset."""
    client, _ = admin_client

    payload: dict[str, Any] = {
        "presets": [
            {
                "label": "default",
                "chain": ["ghost-provider/ghost-model"],
                "is_default": True,
            }
        ],
        "task_presets": {},
    }

    resp = await client.put("/api/v1/admin/model-presets", json=payload)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body.get("error_code") == "broken_preset", body


async def test_put_updates_existing_row(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Second PUT overwrites the first; GET returns the latest value."""
    client, _ = admin_client

    slug, _ = await _create_provider_with_model(
        client, provider_name="update-provider", model_id="m1"
    )
    ref = f"{slug}/m1"

    first = {
        "presets": [{"label": "v1", "chain": [ref], "is_default": True}],
        "task_presets": {},
    }
    resp = await client.put("/api/v1/admin/model-presets", json=first)
    assert resp.status_code == 200, resp.text

    second = {
        "presets": [{"label": "v2", "chain": [ref], "is_default": True}],
        "task_presets": {},
    }
    resp = await client.put("/api/v1/admin/model-presets", json=second)
    assert resp.status_code == 200, resp.text

    resp = await client.get("/api/v1/admin/model-presets")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["origin"] == "org"
    labels = [p["label"] for p in body["value"]["presets"]]
    assert labels == ["v2"]


async def test_non_admin_member_forbidden(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """A workspace MEMBER (not admin) cannot read or write the org row."""
    client, _ = member_client

    resp = await client.get("/api/v1/admin/model-presets")
    assert resp.status_code == 403, resp.text

    resp = await client.put(
        "/api/v1/admin/model-presets",
        json={
            "presets": [{"label": "x", "chain": ["a/b"], "is_default": True}],
            "task_presets": {},
        },
    )
    assert resp.status_code == 403, resp.text
