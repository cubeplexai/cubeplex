"""E2E tests for the admin LLM preset-catalog endpoint."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.e2e  # ensure marker even though conftest auto-adds

# Tracks the cubepi bundled provider-preset catalog (feat branch). Bump if the
# upstream catalog changes.
EXPECTED_PRESET_COUNT = 37


async def test_list_provider_presets(
    admin_client: tuple[AsyncClient, str],
) -> None:
    """Admin sees the full cubepi preset catalog with the expected shape."""
    client, _ws_id = admin_client

    res = await client.get("/api/v1/admin/llm/presets")
    assert res.status_code == 200
    data = res.json()

    assert isinstance(data, list)
    assert len(data) == EXPECTED_PRESET_COUNT

    anthropic = next(p for p in data if p["slug"] == "anthropic")
    assert anthropic["api"] == "anthropic-messages"
    assert anthropic["logo"] == "anthropic"
    assert anthropic["capability"]["reasoning_level"]["kind"] == "int_budget"


async def test_get_provider_returns_liveness_and_per_model_readiness(
    admin_client: tuple[AsyncClient, str],
) -> None:
    """GET /admin/providers/{id} carries liveness + capability + per-model readiness.

    A freshly created provider+model has never been probed, so liveness is null
    and the model's readiness must be "ready" per the never-tested decision.
    """
    client, _ws_id = admin_client

    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "readiness-provider-e2e",
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
            "model_id": "readiness-model-1",
            "display_name": "Readiness Model",
            "context_window": 200000,
            "max_tokens": 64000,
        },
    )
    assert res.status_code == 201

    res = await client.get(f"/api/v1/admin/providers/{pid}")
    assert res.status_code == 200
    data = res.json()

    # Provider-level capability + liveness fields are present.
    assert "capability" in data
    assert "model_capability_overrides" in data
    assert "last_liveness_at" in data
    assert "last_liveness_status" in data
    assert "last_liveness_summary" in data
    # Never probed -> null liveness.
    assert data["last_liveness_at"] is None
    assert data["last_liveness_status"] is None

    # Per-model status + server-derived readiness.
    assert len(data["models"]) == 1
    model = data["models"][0]
    assert model["model_id"] == "readiness-model-1"
    assert "last_test_at" in model
    assert "last_test_status" in model
    assert "last_test_summary" in model
    assert model["last_test_at"] is None
    assert model["last_test_status"] is None
    # Never-tested seeded model -> "ready" (presumed usable).
    assert model["readiness"] == "ready"

    await client.delete(f"/api/v1/admin/providers/{pid}")
