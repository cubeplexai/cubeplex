import httpx

from cubeplex.llm.discovery_metadata import enrich_models
from cubeplex.llm.discovery_resolver import resolve_models
from cubeplex.llm.discovery_transport import DiscoveryTransport
from cubeplex.llm.discovery_types import CallingConfiguration, DiscoveryPolicy


async def test_cliproxy_enrichment_uses_the_local_catalog_without_extra_requests(monkeypatch):
    def lookup(model_id, index=None):
        assert index is None
        if model_id == "claude-opus-4-6-thinking":
            return (
                "anthropic",
                {
                    "context": 200000,
                    "output": 8192,
                    "input": ["text"],
                    "output_modalities": ["text"],
                    "reasoning": True,
                    "cost_input": 15,
                },
            )
        return None

    monkeypatch.setattr("cubeplex.llm.models_dev.lookup_model", lookup)
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(500, text="calling-secret")

    config = CallingConfiguration(base_url="https://endpoint.test/v1", api_key="calling-secret")
    policy = DiscoveryPolicy(
        adapter="openai",
        auth="bearer",
        metadata_profile="cliproxyapi",
        reference="https://example.test",
    )
    rows = [{"id": "claude-opus-4-6-thinking"}, {"id": "grok-tts"}]
    models = resolve_models(rows)
    async with DiscoveryTransport(config, policy, transport=httpx.MockTransport(respond)) as http:
        warnings = await enrich_models(models, rows, config, policy, http, selected_ids=[])
    assert calls == [] and not warnings
    matched, unmatched = models
    assert matched.fields["context_window"].state == "declared"
    assert matched.fields["context_window"].source == "models.dev:anthropic"
    assert matched.fields["context_window"].value == 200000
    assert matched.fields["max_tokens"].value == 8192
    assert matched.fields["cost_input"].value == 15
    assert matched.eligible
    assert not unmatched.eligible
    assert "context_window" not in unmatched.fields
    dumped = matched.model_dump_json()
    assert "calling-secret" not in dumped


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
