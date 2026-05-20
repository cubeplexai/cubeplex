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


async def test_probe_dryrun_returns_step_summary(
    admin_client: tuple[AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-save /providers/test composes liveness + model probe into one ProbeResult."""
    from cubebox.services import provider_probe
    from cubebox.services.provider_probe import ProbeResult, ProbeStep

    async def _fake_liveness(**_: object) -> ProbeStep:
        return ProbeStep(name="liveness", status="pass", latency_ms=7, detail="ok")

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
            "api_key": "sk-test",
            "capability": {},
            "model_id": "gpt-test",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["overall"] == "pass"
    assert body["steps"][0]["name"] == "liveness"


async def test_liveness_dryrun_does_not_persist(
    admin_client: tuple[AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-save /providers/liveness writes no row and changes no provider."""
    from cubebox.services import provider_probe
    from cubebox.services.provider_probe import ProbeStep

    async def _fake_liveness(**_: object) -> ProbeStep:
        return ProbeStep(name="liveness", status="pass", latency_ms=5)

    monkeypatch.setattr(provider_probe, "run_liveness", _fake_liveness)

    client, _ws_id = admin_client
    before = (await client.get("/api/v1/admin/providers")).json()

    res = await client.post(
        "/api/v1/admin/providers/liveness",
        json={
            "api": "openai-completions",
            "base_url": "https://example.com/api",
            "api_key": "sk-test",
            "capability": {},
            "model_id": "gpt-test",
        },
    )
    assert res.status_code == 200
    assert res.json()["name"] == "liveness"
    assert res.json()["status"] == "pass"

    after = (await client.get("/api/v1/admin/providers")).json()
    # No row created or mutated by a dry-run.
    assert len(after) == len(before)


async def test_saved_model_test_persists_status_and_fingerprint(
    admin_client: tuple[AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Saved /{id}/models/{mid}/test persists model status, liveness, and fingerprint."""
    from cubebox.services import provider_probe
    from cubebox.services.provider_probe import ProbeResult, ProbeStep

    async def _fake_liveness(**_: object) -> ProbeStep:
        return ProbeStep(name="liveness", status="pass", latency_ms=9)

    async def _fake_model_probe(**_: object) -> ProbeResult:
        return ProbeResult(
            overall="warn",
            blocking_failed=False,
            steps=[ProbeStep(name="reasoning", status="pass")],
        )

    monkeypatch.setattr(provider_probe, "run_liveness", _fake_liveness)
    monkeypatch.setattr(provider_probe, "run_model_probe", _fake_model_probe)

    client, _ws_id = admin_client
    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "persist-provider-e2e",
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
            "model_id": "persist-model-1",
            "display_name": "Persist Model",
            "context_window": 200000,
            "max_tokens": 64000,
        },
    )
    assert res.status_code == 201
    mid = res.json()["id"]

    res = await client.post(f"/api/v1/admin/providers/{pid}/models/{mid}/test")
    assert res.status_code == 200
    result = res.json()
    assert result["overall"] == "warn"
    assert result["steps"][0]["name"] == "liveness"

    # Re-read: status persisted on both provider (liveness) and model (test).
    data = (await client.get(f"/api/v1/admin/providers/{pid}")).json()
    assert data["last_liveness_status"] == "ok"
    model = data["models"][0]
    # ProbeResult.overall "warn" -> last_test_status "warn".
    assert model["last_test_status"] == "warn"
    assert model["last_test_at"] is not None
    # Fingerprint REQUIRED for Task 5 stale detection.
    assert model["last_test_summary"].get("capability_fingerprint")

    await client.delete(f"/api/v1/admin/providers/{pid}")
