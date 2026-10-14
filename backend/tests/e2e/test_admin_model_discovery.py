"""Discovery/import business flow against real FastAPI, Postgres and Redis."""

import json

import httpx
import pytest

from cubeplex.llm.discovery_transport import DiscoveryTransport


@pytest.fixture
def llm_transport(monkeypatch):
    from cubeplex.services.provider_service import ProviderService

    original = ProviderService._provider_factory_from_config
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        delta = {"content": "OK"}
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
