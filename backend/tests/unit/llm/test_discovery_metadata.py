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
