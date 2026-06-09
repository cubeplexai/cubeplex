"""E2E tests for /api/v1/ws/{workspace_id}/model-presets.

Workspace-side listing endpoint: returns the effective preset list
(label + is_default) — org row if present, else system row, else empty.
Chain refs are NOT exposed.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.e2e  # belt + suspenders alongside conftest auto-mark


async def _seed_preset(
    admin: httpx.AsyncClient,
    *,
    provider_name: str,
    model_id: str,
    label: str,
) -> str:
    """Create one provider+model, then PUT an org-level preset using that ref.

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
    resp = await admin.put(
        "/api/v1/admin/model-presets",
        json={
            "presets": [{"label": label, "chain": [ref], "is_default": True}],
            "task_presets": {},
        },
    )
    assert resp.status_code == 200, resp.text
    return ref


async def test_member_sees_effective_preset_list(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Admin (also a member) GETs the workspace listing after seeding one preset."""
    client, ws_id = admin_client
    await _seed_preset(client, provider_name="ws-list-provider", model_id="m1", label="primary")

    resp = await client.get(f"/api/v1/ws/{ws_id}/model-presets")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "presets" in body
    labels = [p["label"] for p in body["presets"]]
    assert "primary" in labels, body
    primary = next(p for p in body["presets"] if p["label"] == "primary")
    assert primary["is_default"] is True
    # Chain refs MUST NOT leak through the workspace endpoint.
    assert "chain" not in primary


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


async def test_member_sees_empty_list_when_no_presets(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Fresh member workspace with no admin-written presets returns an empty
    list (or whatever the system fallback provides) without 5xx-ing."""
    client, ws_id = member_client

    resp = await client.get(f"/api/v1/ws/{ws_id}/model-presets")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["presets"], list)
    # Don't assert empty: bootstrap may seed a system-level row. The
    # contract is 200 + well-formed shape.
    for p in body["presets"]:
        assert "label" in p
        assert "is_default" in p
        assert "chain" not in p
