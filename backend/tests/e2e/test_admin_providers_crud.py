"""E2E tests for admin provider/model CRUD endpoints."""

from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.e2e  # ensure marker even though conftest auto-adds


def _preset_body(label: str, ref: str) -> dict[str, Any]:
    """A valid tiered model-presets PUT body: one custom preset (the default).

    Tiers are all off; the named custom preset points at `ref` so the
    delete-guard scan reports `label` when its model is referenced.
    """
    off = {"enabled": False, "primary": None, "fallbacks": []}
    return {
        "tiers": {
            "lite": dict(off),
            "flash": dict(off),
            "pro": dict(off),
            "max": dict(off),
        },
        "custom_presets": [{"label": label, "primary": ref, "fallbacks": [], "description": ""}],
        "default_preset": label,
        "task_routing": {},
    }


async def test_create_and_list_providers(
    admin_client: tuple[AsyncClient, str],
) -> None:
    """Admin can create an org provider and see it in the list."""
    client, _ws_id = admin_client

    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "test-provider-e2e",
            "base_url": "https://example.com/api",
            "auth_type": "api_key",
            "api_key": "sk-test-123",
            "provider_type": "openai-completions",
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "test-provider-e2e"
    assert data["is_system"] is False
    assert data["has_api_key"] is True

    provider_id = data["id"]

    # List
    res = await client.get("/api/v1/admin/providers")
    assert res.status_code == 200
    providers = res.json()
    assert any(p["id"] == provider_id for p in providers)

    # Delete
    res = await client.delete(f"/api/v1/admin/providers/{provider_id}")
    assert res.status_code == 204


async def test_provider_name_conflict(
    admin_client: tuple[AsyncClient, str],
) -> None:
    """Duplicate provider name returns 409."""
    client, _ws_id = admin_client

    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "dup-provider-e2e",
            "base_url": "https://example.com/api",
            "auth_type": "none",
        },
    )
    assert res.status_code == 201

    res2 = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "dup-provider-e2e",
            "base_url": "https://other.com/api",
            "auth_type": "none",
        },
    )
    assert res2.status_code == 409

    # Cleanup
    pid = res.json()["id"]
    await client.delete(f"/api/v1/admin/providers/{pid}")


async def test_model_crud(admin_client: tuple[AsyncClient, str]) -> None:
    """Admin can create, list, update, and delete models on their org provider."""
    client, _ws_id = admin_client

    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "model-test-provider-e2e",
            "base_url": "https://example.com/api",
            "auth_type": "api_key",
            "api_key": "sk-test",
        },
    )
    assert res.status_code == 201
    pid = res.json()["id"]

    res = await client.post(
        f"/api/v1/admin/providers/{pid}/models",
        json={
            "model_id": "test-model-1",
            "display_name": "Test Model",
            "reasoning": True,
            "input_modalities": ["text", "image"],
            "cost_input": 3.0,
            "cost_output": 15.0,
            "context_window": 200000,
            "max_tokens": 64000,
        },
    )
    assert res.status_code == 201
    model_data = res.json()
    assert model_data["model_id"] == "test-model-1"
    mid = model_data["id"]

    # Update
    res = await client.patch(
        f"/api/v1/admin/providers/{pid}/models/{mid}",
        json={"display_name": "Updated Model"},
    )
    assert res.status_code == 200
    assert res.json()["display_name"] == "Updated Model"

    # Delete
    res = await client.delete(f"/api/v1/admin/providers/{pid}/models/{mid}")
    assert res.status_code == 204

    await client.delete(f"/api/v1/admin/providers/{pid}")


async def test_delete_model_blocked_by_preset_reference(
    admin_client: tuple[AsyncClient, str],
) -> None:
    """Admin attempting to delete a model referenced by their org's preset row gets 409.

    Per D6: only the caller's org row is scanned (system row + other orgs are
    intentionally skipped).
    """
    client, _ws_id = admin_client

    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "guard-test-provider-e2e",
            "base_url": "https://example.com/api",
            "auth_type": "api_key",
            "api_key": "sk-test",
        },
    )
    assert res.status_code == 201
    provider = res.json()
    pid = provider["id"]
    slug = provider["slug"]

    res = await client.post(
        f"/api/v1/admin/providers/{pid}/models",
        json={
            "model_id": "guard-m1",
            "display_name": "Guard Test",
            "context_window": 128000,
            "max_tokens": 4096,
        },
    )
    assert res.status_code == 201
    mid = res.json()["id"]
    ref = f"{slug}/guard-m1"

    # Org admin writes a preset that references this model.
    res = await client.put(
        "/api/v1/admin/model-presets",
        json=_preset_body("in-use", ref),
    )
    assert res.status_code == 200, res.text

    # Now deleting the referenced model must fail with 409 + label list.
    res = await client.delete(f"/api/v1/admin/providers/{pid}/models/{mid}")
    assert res.status_code == 409, res.text
    body = res.json()
    assert body.get("error_code") == "model_in_use_by_preset", body
    assert "in-use" in body.get("details", "")

    # Repoint the preset chain at a different (auto-seeded system) ref so the
    # caller-org row no longer references our model; delete should now succeed.
    # We pick an unrelated dummy ref by first creating a second model on the
    # same provider, repointing presets at it, then deleting our model.
    res = await client.post(
        f"/api/v1/admin/providers/{pid}/models",
        json={
            "model_id": "guard-m2",
            "display_name": "Guard Test 2",
            "context_window": 128000,
            "max_tokens": 4096,
        },
    )
    assert res.status_code == 201
    mid2 = res.json()["id"]
    other_ref = f"{slug}/guard-m2"
    res = await client.put(
        "/api/v1/admin/model-presets",
        json=_preset_body("moved", other_ref),
    )
    assert res.status_code == 200, res.text
    res = await client.delete(f"/api/v1/admin/providers/{pid}/models/{mid}")
    assert res.status_code == 204, res.text

    # Cleanup
    await client.delete(f"/api/v1/admin/providers/{pid}/models/{mid2}")
    await client.delete(f"/api/v1/admin/providers/{pid}")


async def test_system_provider_not_deletable(
    admin_client: tuple[AsyncClient, str],
) -> None:
    """System providers cannot be deleted or have models added."""
    client, _ws_id = admin_client

    res = await client.get("/api/v1/admin/providers")
    system = [p for p in res.json() if p["is_system"]]
    if not system:
        pytest.skip("No system providers available")
    sys_id = system[0]["id"]

    # Cannot delete
    res = await client.delete(f"/api/v1/admin/providers/{sys_id}")
    assert res.status_code == 403

    # Cannot add models
    res = await client.post(
        f"/api/v1/admin/providers/{sys_id}/models",
        json={
            "model_id": "hacker-model",
            "display_name": "Hack",
            "context_window": 128000,
            "max_tokens": 64000,
        },
    )
    assert res.status_code == 403


async def test_test_connection_endpoint(
    admin_client: tuple[AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-save /providers/test returns a composed ProbeResult (no DB write)."""
    from cubebox.services import provider_probe
    from cubebox.services.provider_probe import ProbeResult, ProbeStep

    async def _fake_liveness(**_: object) -> ProbeStep:
        return ProbeStep(name="liveness", status="pass", latency_ms=12, detail="ok")

    async def _fake_model_probe(**_: object) -> ProbeResult:
        return ProbeResult(
            overall="pass",
            blocking_failed=False,
            steps=[ProbeStep(name="reasoning", status="pass")],
        )

    monkeypatch.setattr(provider_probe, "run_liveness", _fake_liveness)
    monkeypatch.setattr(provider_probe, "run_model_probe", _fake_model_probe)

    client, _ws_id = admin_client

    res = await client.post(
        "/api/v1/admin/providers/test",
        json={
            "api": "openai-completions",
            "base_url": "https://example.com/api",
            "api_key": "test",
            "capability": {},
            "model_id": "gpt-test",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["overall"] == "pass"
    assert data["steps"][0]["name"] == "liveness"


async def test_create_model_disabled(admin_client: tuple[AsyncClient, str]) -> None:
    """Admin can create a model with enabled=false."""
    client, _ = admin_client
    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "b2-model-disabled",
            "provider_type": "openai-completions",
            "base_url": "https://example.com",
            "auth_type": "api_key",
            "api_key": "sk-x",
        },
    )
    assert res.status_code == 201
    pid = res.json()["id"]
    res = await client.post(
        f"/api/v1/admin/providers/{pid}/models",
        json={
            "model_id": "m-disabled",
            "display_name": "M",
            "context_window": 8192,
            "max_tokens": 1024,
            "enabled": False,
        },
    )
    assert res.status_code == 201
    assert res.json()["enabled"] is False
    await client.delete(f"/api/v1/admin/providers/{pid}")


async def test_create_provider_persists_capability(
    admin_client: tuple[AsyncClient, str],
) -> None:
    client, _ = admin_client
    cap = {"reasoning_off_payload": {"thinking": {"type": "disabled"}}}
    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "cap-create-e2e",
            "provider_type": "anthropic-messages",
            "base_url": "https://example.com",
            "auth_type": "api_key",
            "api_key": "sk-x",
            "preset_slug": "anthropic",
            "capability": cap,
            "model_capability_overrides": {},
        },
    )
    assert res.status_code == 201
    pid = res.json()["id"]
    got = (await client.get(f"/api/v1/admin/providers/{pid}")).json()
    assert got["preset_slug"] == "anthropic"
    assert got["capability"] == cap
    await client.delete(f"/api/v1/admin/providers/{pid}")


async def test_provider_out_resolves_brand_logo_from_preset(
    admin_client: tuple[AsyncClient, str],
) -> None:
    """A provider with a known preset_slug (preset_key) exposes its brand-icon id as `logo`."""
    client, _ = admin_client
    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "logo-resolve-e2e",
            "provider_type": "anthropic-messages",
            "base_url": "https://example.com",
            "auth_type": "api_key",
            "api_key": "sk-x",
            "preset_slug": "anthropic/intl/anthropic-messages",
        },
    )
    assert res.status_code == 201
    pid = res.json()["id"]
    got = (await client.get(f"/api/v1/admin/providers/{pid}")).json()
    assert got["logo"] == "anthropic"
    await client.delete(f"/api/v1/admin/providers/{pid}")


async def test_create_provider_derives_slug_from_name(
    admin_client: tuple[AsyncClient, str],
) -> None:
    client, _ = admin_client
    body = {
        "name": "My DeepSeek",
        "provider_type": "openai-completions",
        "base_url": "https://x.test/v1",
        "auth_type": "api_key",
        "api_key": "k",
    }
    r = await client.post("/api/v1/admin/providers", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["slug"] == "my-deepseek"
    await client.delete(f"/api/v1/admin/providers/{r.json()['id']}")


async def test_create_provider_explicit_slug_and_conflict(
    admin_client: tuple[AsyncClient, str],
) -> None:
    client, _ = admin_client
    base = {
        "provider_type": "openai-completions",
        "base_url": "https://x.test/v1",
        "auth_type": "api_key",
        "api_key": "k",
    }
    r1 = await client.post("/api/v1/admin/providers", json={**base, "name": "A", "slug": "shared"})
    assert r1.status_code == 201
    assert r1.json()["slug"] == "shared"
    r2 = await client.post("/api/v1/admin/providers", json={**base, "name": "B", "slug": "shared"})
    assert r2.status_code == 409
    await client.delete(f"/api/v1/admin/providers/{r1.json()['id']}")


async def test_create_provider_auto_slug_suffixes_on_collision(
    admin_client: tuple[AsyncClient, str],
) -> None:
    client, _ = admin_client
    base = {
        "provider_type": "openai-completions",
        "base_url": "https://x.test/v1",
        "auth_type": "api_key",
        "api_key": "k",
    }
    r1 = await client.post("/api/v1/admin/providers", json={**base, "name": "Dup Name"})
    r2 = await client.post("/api/v1/admin/providers", json={**base, "name": "Dup  Name"})
    assert {r1.json()["slug"], r2.json()["slug"]} == {"dup-name", "dup-name-2"}
    await client.delete(f"/api/v1/admin/providers/{r1.json()['id']}")
    await client.delete(f"/api/v1/admin/providers/{r2.json()['id']}")


@pytest.mark.parametrize("bad", ["Has Space", "UPPER", "trailing-", "has/slash", ""])
async def test_create_provider_rejects_malformed_explicit_slug(
    admin_client: tuple[AsyncClient, str],
    bad: str,
) -> None:
    client, _ = admin_client
    body = {
        "name": "Whatever",
        "provider_type": "openai-completions",
        "base_url": "https://x.test/v1",
        "auth_type": "api_key",
        "api_key": "k",
        "slug": bad,
    }
    r = await client.post("/api/v1/admin/providers", json=body)
    assert r.status_code == 422


async def test_provider_slug_round_trips(
    admin_client: tuple[AsyncClient, str],
) -> None:
    client, _ = admin_client
    body = {
        "name": "Round Trip",
        "provider_type": "openai-completions",
        "base_url": "https://x.test/v1",
        "auth_type": "api_key",
        "api_key": "k",
    }
    created = (await client.post("/api/v1/admin/providers", json=body)).json()
    assert created["slug"] == "round-trip"
    fetched = (await client.get(f"/api/v1/admin/providers/{created['id']}")).json()
    assert fetched["slug"] == "round-trip"
    await client.delete(f"/api/v1/admin/providers/{created['id']}")
