"""E2E tests for admin provider/model CRUD endpoints."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.e2e  # ensure marker even though conftest auto-adds


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
            "provider_type": "openai_compat",
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


async def test_org_settings(admin_client: tuple[AsyncClient, str]) -> None:
    """Admin can set and read org LLM settings."""
    client, _ws_id = admin_client

    res = await client.get("/api/v1/admin/settings/llm")
    assert res.status_code == 200

    res = await client.put(
        "/api/v1/admin/settings/llm",
        json={
            "default_model": None,
            "fallback_models": [],
        },
    )
    assert res.status_code == 200


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


async def test_config_fallback(
    admin_client: tuple[AsyncClient, str],
) -> None:
    """LLMFactory resolves the default provider/model from config when DB
    has providers."""
    from cubebox.llm.factory import LLMFactory

    factory = LLMFactory()
    provider_name, model_id, provider_config = await factory.resolve_default_provider_and_config()
    assert provider_name
    assert model_id
    assert provider_config is not None


async def test_test_connection_endpoint(
    admin_client: tuple[AsyncClient, str],
) -> None:
    """Test connection endpoint returns structured result."""
    client, _ws_id = admin_client

    res = await client.post(
        "/api/v1/admin/providers/test",
        json={
            "provider_type": "openai_compat",
            "base_url": "https://httpbin.org/post",
            "api_key": "test",
            "auth_type": "api_key",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "ok" in data
    assert "latency_ms" in data
