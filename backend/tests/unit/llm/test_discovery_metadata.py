import httpx

from cubeplex.llm.discovery_metadata import enrich_models
from cubeplex.llm.discovery_resolver import resolve_models
from cubeplex.llm.discovery_transport import DiscoveryTransport
from cubeplex.llm.discovery_types import CallingConfiguration, DiscoveryPolicy


async def test_management_enrichment_obtains_credential_names_and_keeps_availability_scoped():
    paths = []

    def respond(request):
        paths.append(request.url.path)
        if "client_version" in request.url.params:
            assert request.headers["authorization"] == "Bearer calling-key"
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "slug": "m",
                            "context_window": 500000,
                            "supported_reasoning_levels": [{"effort": "high"}, {"effort": "ultra"}],
                        }
                    ]
                },
            )
        assert request.headers["authorization"] == "Bearer management-key"
        if request.url.path.endswith("/credentials"):
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "account name.json",
                            "provider": "codex",
                            "email": "private@example.test",
                        }
                    ],
                    "has_more": False,
                },
            )
        if request.url.path.endswith("/credentials/models"):
            assert request.url.params["name"] == "account name.json"
            return httpx.Response(200, json={"models": [{"id": "m"}, {"id": "hidden"}]})
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "id": "m",
                        "context_length": 272000,
                        "max_completion_tokens": 8192,
                        "supportedInputModalities": ["TEXT"],
                        "supportedOutputModalities": ["TEXT"],
                        "thinking": {"levels": ["high"]},
                    },
                    {"id": "hidden", "context_length": 8192},
                ]
            },
        )

    config = CallingConfiguration(base_url="https://endpoint.test/v1", api_key="calling-key")
    policy = DiscoveryPolicy(
        adapter="openai",
        auth="bearer",
        metadata_profile="cliproxyapi",
        reference="https://example.test",
    )
    rows = [{"id": "m"}]
    models = resolve_models(rows)
    async with DiscoveryTransport(config, policy, transport=httpx.MockTransport(respond)) as http:
        warnings = await enrich_models(
            models, rows, config, policy, http, selected_ids=[], management_key="management-key"
        )
    assert not warnings
    assert [model.model_id for model in models] == ["m"]
    assert models[0].fields["context_window"].state == "conflict"
    assert models[0].fields["reasoning_levels"].state == "conflict"
    dumped = models[0].model_dump_json()
    assert "private@example.test" not in dumped
    assert "calling-key" not in dumped and "management-key" not in dumped
    assert paths[-1] == "/v8/management/routing/model-definitions/codex"


def test_openrouter_prices_are_converted_without_treating_missing_or_invalid_rates_as_free():
    from cubeplex.llm.discovery_metadata import apply_openrouter

    model = resolve_models([{"id": "exact/alias"}])[0]
    apply_openrouter(
        model,
        {
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "top_provider": {"max_completion_tokens": 2048},
            "pricing": {"prompt": "0.000002", "completion": "NaN", "input_cache_read": "0"},
        },
    )
    assert model.fields["cost_input"].value == 2
    assert model.fields["cost_cache_read"].value == 0
    assert "cost_output" not in model.fields and "cost_cache_write" not in model.fields
    assert model.fields["max_tokens"].value == 2048
    assert model.purpose == "chat"


async def test_ollama_enriches_only_selected_exact_names_and_preserves_missing_output_limits():
    calls = []
    config = CallingConfiguration(base_url="http://localhost:11434/v1", auth_type="none")
    policy = DiscoveryPolicy(
        adapter="ollama", metadata_profile="ollama", reference="https://docs.ollama.com/api/show"
    )
    rows = [{"id": "alias:tag"}, {"id": "other:tag"}]
    models = resolve_models(rows)

    def respond(request):
        import json

        calls.append(json.loads(request.content))
        assert request.url.path == "/api/show"
        return httpx.Response(
            200,
            json={
                "model_info": {"llama.context_length": 32768},
                "capabilities": ["completion", "vision"],
            },
        )

    async with DiscoveryTransport(config, policy, transport=httpx.MockTransport(respond)) as http:
        warnings = await enrich_models(
            models, rows, config, policy, http, selected_ids=["alias:tag"]
        )
    assert not warnings and calls == [{"model": "alias:tag"}]
    assert set(models[0].fields["input_modalities"].value) == {"text", "image"}
    assert models[0].fields["context_window"].value == 32768
    assert models[0].missing_fields == ["max_tokens"]
    assert not models[1].fields


async def test_optional_management_failure_retains_base_listing_without_credentials():
    config = CallingConfiguration(base_url="https://endpoint.test/v1", api_key="calling-secret")
    policy = DiscoveryPolicy(
        adapter="openai",
        metadata_profile="cliproxyapi",
        auth="bearer",
        reference="https://example.test",
    )
    rows = [{"id": "alias"}]
    models = resolve_models(rows)

    def respond(request):
        if "client_version" in request.url.params:
            return httpx.Response(200, json={"models": []})
        return httpx.Response(401, text="management-secret")

    async with DiscoveryTransport(config, policy, transport=httpx.MockTransport(respond)) as http:
        warnings = await enrich_models(
            models, rows, config, policy, http, selected_ids=[], management_key="management-secret"
        )
    assert [model.model_id for model in models] == ["alias"]
    assert not models[0].eligible
    assert warnings[0].code == "enrichment_warning"
    assert "management-secret" not in warnings[0].message


def test_preview_hmac_binds_credentials_settings_and_server_secret():
    from cubeplex.services.provider_discovery_preview import preview_fingerprint

    config = CallingConfiguration(base_url="http://localhost:8317/v1", api_key="calling-secret")
    fingerprint = preview_fingerprint(config, "server-secret")
    assert fingerprint == preview_fingerprint(config.model_copy(), "server-secret")
    assert fingerprint != preview_fingerprint(config, "rotated-server-secret")
    assert fingerprint != preview_fingerprint(
        config.model_copy(update={"api_key": "rotated"}), "server-secret"
    )
    assert fingerprint != preview_fingerprint(
        config.model_copy(update={"extra_body": {"seed": 1}}), "server-secret"
    )
    assert len(fingerprint) == 64 and "calling-secret" not in fingerprint
