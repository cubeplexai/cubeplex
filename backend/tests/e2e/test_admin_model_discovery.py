"""Discovery/import business flow against real FastAPI, Postgres and Redis."""

import json

import httpx
import pytest

from cubeplex.llm.discovery_transport import DiscoveryTransport


@pytest.fixture
def llm_transport(monkeypatch, request):
    from cubeplex.services.provider_service import ProviderService

    original = ProviderService._provider_factory_from_config
    requests = []
    auth_failure = getattr(request, "param", None) == "tool_auth_failure"
    vision_transition = getattr(request, "param", None) == "vision_transition"
    vision_responses = 0

    def respond(request):
        nonlocal vision_responses
        body = json.loads(request.content)
        requests.append(body)
        if auth_failure and body["model"] == "unknown-alias" and body.get("tools"):
            return httpx.Response(401, json={"error": {"message": "credential rejected"}})
        delta = {"content": "OK"}
        if vision_transition and any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for message in body.get("messages", [])
            for part in (
                message.get("content", []) if isinstance(message.get("content"), list) else []
            )
        ):
            vision_responses += 1
            delta = {"content": "yellow blue red green" if vision_responses == 1 else "OK"}
        if body.get("tools"):
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "echo-call",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"text":"ping"}'},
                    }
                ]
            }
        chunk = {
            "id": "completion",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": body["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        ending = {
            **chunk,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        content = f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(ending)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=content)

    def factory_for(self, config, *, provider_name, model_id=None):
        factory = original(self, config, provider_name=provider_name, model_id=model_id)

        def make():
            provider = factory()
            provider._client = provider._client.with_options(
                http_client=httpx.AsyncClient(
                    transport=httpx.MockTransport(respond), auth=provider._client._client.auth
                )
            )
            return provider

        return make

    monkeypatch.setattr(ProviderService, "_provider_factory_from_config", factory_for)
    return requests


@pytest.fixture
def model_listing(monkeypatch):
    from cubeplex.services import provider_discovery

    def respond(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "alias/chat",
                        "purpose": "chat",
                        "context_length": 32768,
                        "max_tokens": 1024,
                        "input_modalities": ["text"],
                        "reasoning": False,
                    },
                    {"id": "unknown-alias"},
                ]
            },
        )

    monkeypatch.setattr(
        provider_discovery,
        "DiscoveryTransport",
        lambda config, policy: DiscoveryTransport(
            config, policy, transport=httpx.MockTransport(respond)
        ),
    )


async def test_preview_create_import_retry_and_activation_gate(admin_client, model_listing):
    client, _ = admin_client
    config = {
        "api": "openai-completions",
        "base_url": "http://127.0.0.1:8317/v1",
        "auth_type": "none",
    }
    before = (await client.get("/api/v1/admin/providers")).json()
    response = await client.post("/api/v1/admin/providers/discover", json=config)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["status"] == "complete"
    assert preview["candidates"][0]["eligible"]
    assert not preview["candidates"][1]["eligible"]
    assert (await client.get("/api/v1/admin/providers")).json() == before
    created = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "discovery-flow",
            "base_url": config["base_url"],
            "auth_type": "none",
            "discovery_id": preview["discovery_id"],
        },
    )
    assert created.status_code == 201, created.text
    provider = created.json()
    try:
        recovered = await client.post(
            "/api/v1/admin/providers",
            json={
                "name": "different-retry-name",
                "base_url": "http://localhost:9999/v1",
                "auth_type": "none",
                "discovery_id": preview["discovery_id"],
            },
        )
        assert recovered.status_code == 201, recovered.text
        assert recovered.json()["id"] == provider["id"]
        assert recovered.json()["creation_recovered"]
        path = f"/api/v1/admin/providers/{provider['id']}/models/import"
        body = {"discovery_id": preview["discovery_id"], "models": [{"model_id": "alias/chat"}]}
        imported = await client.post(path, json=body)
        assert imported.status_code == 200, imported.text
        model_id = imported.json()["results"][0]["model_db_id"]
        retry = await client.post(path, json=body)
        assert retry.status_code == 200
        assert retry.json()["results"] == [
            {"model_id": "alias/chat", "model_db_id": model_id, "status": "existing"}
        ]
        saved = (await client.get(f"/api/v1/admin/providers/{provider['id']}")).json()
        assert len(saved["models"]) == 1
        assert saved["models"][0]["enabled"] is False
        enabled = await client.post(
            f"/api/v1/admin/providers/{provider['id']}/models/enable-tested",
            json={"model_db_ids": [model_id]},
        )
        assert enabled.status_code == 200
        assert enabled.json()["results"][0]["status"] == "blocked"
        direct = await client.patch(
            f"/api/v1/admin/providers/{provider['id']}/models/{model_id}",
            json={"enabled": True, "expected_configuration_revision": 1},
        )
        assert direct.status_code == 409
    finally:
        assert (await client.delete(f"/api/v1/admin/providers/{provider['id']}")).status_code == 204
    deleted_retry = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "discovery-flow",
            "base_url": config["base_url"],
            "auth_type": "none",
            "discovery_id": preview["discovery_id"],
        },
    )
    assert deleted_retry.status_code == 409
    assert deleted_retry.json()["detail"]["code"] == "provider_creation_deleted"


async def test_verified_import_and_continued_use_after_edit(
    admin_client, model_listing, llm_transport
):
    client, _ = admin_client
    config = {
        "api": "openai-completions",
        "base_url": "http://127.0.0.1:8317/v1",
        "auth_type": "none",
    }
    preview = (await client.post("/api/v1/admin/providers/discover", json=config)).json()
    created = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "verified-import",
            "base_url": config["base_url"],
            "auth_type": "none",
            "discovery_id": preview["discovery_id"],
        },
    )
    assert created.status_code == 201, created.text
    provider_id = created.json()["id"]
    path = f"/api/v1/admin/providers/{provider_id}"
    try:
        imported = await client.post(
            path + "/models/import",
            json={
                "discovery_id": preview["discovery_id"],
                "models": [{"model_id": "alias/chat"}],
            },
        )
        assert imported.status_code == 200, imported.text
        model_id = imported.json()["results"][0]["model_db_id"]
        tested = await client.post(path + f"/models/{model_id}/test")
        assert tested.status_code == 200, tested.text
        assert tested.json()["overall"] == "pass", tested.text
        assert tested.json()["current"]
        assert llm_transport and all(request["model"] == "alias/chat" for request in llm_transport)
        assert all(request["max_tokens"] <= 1024 for request in llm_transport)
        enabled = await client.post(
            path + "/models/enable-tested", json={"model_db_ids": [model_id]}
        )
        assert enabled.json()["results"][0]["status"] == "enabled", enabled.text
        saved = (await client.get(path)).json()
        model = saved["models"][0]
        updated = await client.patch(
            path + f"/models/{model_id}",
            json={
                "max_tokens": 512,
                "expected_configuration_revision": model["configuration_revision"],
            },
        )
        assert updated.status_code == 200, updated.text
        pending = (await client.get(path)).json()["models"][0]
        assert pending["enabled"] and pending["verification_pending"]
        disabled = await client.patch(
            path + f"/models/{model_id}",
            json={
                "enabled": False,
                "expected_configuration_revision": pending["configuration_revision"],
            },
        )
        assert disabled.status_code == 200
        blocked = await client.post(
            path + "/models/enable-tested", json={"model_db_ids": [model_id]}
        )
        assert blocked.json()["results"][0]["status"] == "blocked"
    finally:
        await client.delete(path)


async def test_invalid_import_rolls_back_entire_batch_and_stale_forms_are_rejected(
    admin_client,
    model_listing,
):
    client, _ = admin_client
    config = {
        "api": "openai-completions",
        "base_url": "http://127.0.0.1:8317/v1",
        "auth_type": "none",
    }
    preview = (await client.post("/api/v1/admin/providers/discover", json=config)).json()
    created = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "atomic-discovery",
            "base_url": config["base_url"],
            "auth_type": "none",
            "discovery_id": preview["discovery_id"],
        },
    )
    assert created.status_code == 201, created.text
    provider = created.json()
    path = f"/api/v1/admin/providers/{provider['id']}"
    try:
        bad = await client.post(
            path + "/models/import",
            json={
                "discovery_id": preview["discovery_id"],
                "models": [{"model_id": "alias/chat"}, {"model_id": "unknown-alias"}],
            },
        )
        assert bad.status_code == 422, bad.text
        assert (await client.get(path)).json()["models"] == []
        edited = await client.patch(
            path,
            json={
                "base_url": "http://localhost:9999/v1",
                "expected_configuration_revision": provider["configuration_revision"],
            },
        )
        assert edited.status_code == 200, edited.text
        stale = await client.patch(
            path,
            json={
                "name": "old-form-overwrite",
                "expected_configuration_revision": provider["configuration_revision"],
            },
        )
        assert stale.status_code == 409
        old_preview = await client.post(
            path + "/models/import",
            json={
                "discovery_id": preview["discovery_id"],
                "models": [{"model_id": "alias/chat"}],
            },
        )
        assert old_preview.status_code == 409
        assert (await client.get(path)).json()["name"] == "atomic-discovery"
    finally:
        await client.delete(path)


async def test_new_image_declaration_requires_vision_proof_even_on_an_enabled_manual_model(
    admin_client,
    db_session,
):
    from sqlalchemy import delete

    from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
    from cubeplex.models.provider import Provider

    client, ws_id = admin_client
    created = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "new-image-proof",
            "base_url": "http://localhost:8317/v1",
            "auth_type": "none",
            "capability": {"supports_images": True},
        },
    )
    assert created.status_code == 201, created.text
    provider = created.json()
    path = f"/api/v1/admin/providers/{provider['id']}"
    try:
        model = (
            await client.post(
                path + "/models",
                json={
                    "model_id": "chat",
                    "display_name": "Chat",
                    "context_window": 8192,
                    "max_tokens": 1024,
                },
            )
        ).json()
        presets = await client.put(
            "/api/v1/admin/model-presets",
            json={
                "tiers": {
                    "pro": {"enabled": True, "primary": provider["slug"] + "/chat", "fallbacks": []}
                },
                "custom_presets": [],
                "default_preset": "pro",
                "task_routing": {},
            },
        )
        assert presets.status_code == 200, presets.text
        edit = await client.patch(
            path + "/models/" + model["id"],
            json={
                "input_modalities": ["text", "image"],
                "expected_configuration_revision": model["configuration_revision"],
            },
        )
        assert edit.status_code == 200, edit.text
        summaries = (await client.get(f"/api/v1/ws/{ws_id}/model-presets")).json()["presets"]
        assert next(p for p in summaries if p["key"] == "pro")["input_modalities"] == ["text"]
        assert (await client.get(path)).json()["models"][0]["enabled"] is True
    finally:
        row = await db_session.get(Provider, provider["id"])
        await db_session.execute(
            delete(OrgSettings).where(
                OrgSettings.org_id == row.org_id, OrgSettings.key == MODEL_PRESETS_KEY
            )
        )
        await db_session.commit()
        await client.delete(path)


@pytest.mark.parametrize("llm_transport", ["tool_auth_failure"], indirect=True)
async def test_model_probe_auth_failure_blocks_earlier_passed_models(
    admin_client,
    model_listing,
    llm_transport,
):
    client, _ = admin_client
    config = {"base_url": "http://localhost:8317/v1", "auth_type": "none"}
    preview = (await client.post("/api/v1/admin/providers/discover", json=config)).json()
    provider = (
        await client.post(
            "/api/v1/admin/providers",
            json={
                "name": "probe-auth-grain",
                **config,
                "discovery_id": preview["discovery_id"],
            },
        )
    ).json()
    path = f"/api/v1/admin/providers/{provider['id']}"
    try:
        imported = await client.post(
            path + "/models/import",
            json={
                "discovery_id": preview["discovery_id"],
                "models": [
                    {"model_id": "alias/chat"},
                    {
                        "model_id": "unknown-alias",
                        "overrides": {
                            "purpose": "chat",
                            "context_window": 8192,
                            "max_tokens": 512,
                            "input_modalities": ["text"],
                        },
                    },
                ],
            },
        )
        assert imported.status_code == 200, imported.text
        ids = [row["model_db_id"] for row in imported.json()["results"]]
        passed = await client.post(path + f"/models/{ids[0]}/test")
        assert passed.json()["overall"] == "pass", passed.text
        rejected = await client.post(path + f"/models/{ids[1]}/test")
        assert rejected.json()["overall"] == "fail", rejected.text
        activation = await client.post(
            path + "/models/enable-tested", json={"model_db_ids": [ids[0]]}
        )
        assert activation.json()["results"][0]["status"] == "blocked"
        assert (await client.get(path)).json()["last_liveness_status"] == "auth_error"
    finally:
        await client.delete(path)


@pytest.mark.parametrize("llm_transport", ["vision_transition"], indirect=True)
async def test_current_negative_vision_retracts_image_support_without_disabling_text(
    admin_client,
    db_session,
    llm_transport,
):
    from sqlalchemy import delete

    from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
    from cubeplex.models.provider import Provider

    client, ws_id = admin_client
    created = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "vision-evidence-update",
            "base_url": "http://localhost:8317/v1",
            "auth_type": "none",
            "capability": {"supports_images": True},
        },
    )
    assert created.status_code == 201, created.text
    provider = created.json()
    path = f"/api/v1/admin/providers/{provider['id']}"
    try:
        model = (
            await client.post(
                path + "/models",
                json={
                    "model_id": "manual-img",
                    "display_name": "Manual image",
                    "context_window": 8192,
                    "max_tokens": 1024,
                    "input_modalities": ["text", "image"],
                },
            )
        ).json()
        configured = await client.put(
            "/api/v1/admin/model-presets",
            json={
                "tiers": {
                    "pro": {
                        "enabled": True,
                        "primary": provider["slug"] + "/manual-img",
                        "fallbacks": [],
                    }
                },
                "custom_presets": [],
                "default_preset": "pro",
                "task_routing": {},
            },
        )
        assert configured.status_code == 200, configured.text
        passed = await client.post(path + f"/models/{model['id']}/test")
        assert passed.json()["overall"] == "pass", passed.text
        edited = await client.patch(
            path,
            json={
                "base_url": "http://localhost:8317/other/v1",
                "expected_configuration_revision": provider["configuration_revision"],
            },
        )
        assert edited.status_code == 200, edited.text
        before = (await client.get(f"/api/v1/ws/{ws_id}/model-presets")).json()["presets"]
        assert "image" in next(p for p in before if p["key"] == "pro")["input_modalities"]
        rejected = await client.post(path + f"/models/{model['id']}/test")
        assert rejected.json()["overall"] == "fail", rejected.text
        after = (await client.get(f"/api/v1/ws/{ws_id}/model-presets")).json()["presets"]
        assert next(p for p in after if p["key"] == "pro")["input_modalities"] == ["text"]
        assert (await client.get(path)).json()["models"][0]["enabled"] is True
    finally:
        row = await db_session.get(Provider, provider["id"])
        await db_session.execute(
            delete(OrgSettings).where(
                OrgSettings.org_id == row.org_id, OrgSettings.key == MODEL_PRESETS_KEY
            )
        )
        await db_session.commit()
        await client.delete(path)
