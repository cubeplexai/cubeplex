import httpx
import pytest

from cubeplex.llm.discovery_adapters import collect_models
from cubeplex.llm.discovery_transport import DiscoveryFailure, DiscoveryTransport
from cubeplex.llm.discovery_types import CallingConfiguration, DiscoveryPolicy


async def test_anthropic_collects_every_page_and_preserves_alias_identity():
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["x-api-key"] == "test-key"
        assert "authorization" not in request.headers
        if "after_id" not in request.url.params:
            return httpx.Response(
                200, json={"data": [{"id": "alias/A"}], "has_more": True, "last_id": "alias/A"}
            )
        assert request.url.params["after_id"] == "alias/A"
        return httpx.Response(200, json={"data": [{"id": "Prefix/B"}], "has_more": False})

    config = CallingConfiguration(base_url="https://endpoint.test", api_key="test-key")
    policy = DiscoveryPolicy(
        adapter="anthropic",
        auth="anthropic",
        pagination="anthropic",
        root_api_path="/v1",
        reference="https://example.test/reference",
    )
    async with DiscoveryTransport(config, policy, transport=httpx.MockTransport(respond)) as http:
        rows = await collect_models(config, policy, http)
    assert [row["id"] for row in rows] == ["alias/A", "Prefix/B"]
    assert len(requests) == 2
    assert requests[0].url.path == "/v1/models"


async def test_repeated_cursor_never_produces_a_complete_collection():
    config = CallingConfiguration(base_url="https://endpoint.test", auth_type="none")
    policy = DiscoveryPolicy(
        adapter="anthropic",
        auth="anthropic",
        pagination="anthropic",
        reference="https://example.test/reference",
    )
    mock = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"data": [{"id": "m"}], "has_more": True, "last_id": "m"}
        )
    )
    async with DiscoveryTransport(config, policy, transport=mock) as http:
        with pytest.raises(DiscoveryFailure, match="cursor"):
            await collect_models(config, policy, http)


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_error"),
        (404, "not_supported"),
        (429, "rate_limited"),
        (302, "upstream_error"),
    ],
)
async def test_listing_failure_is_typed_and_sanitized(status, code):
    config = CallingConfiguration(base_url="https://endpoint.test", api_key="secret-key")
    policy = DiscoveryPolicy(adapter="openai", auth="bearer", reference="https://example.test")
    mock = httpx.MockTransport(lambda request: httpx.Response(status, text="secret-key"))
    async with DiscoveryTransport(config, policy, transport=mock) as http:
        with pytest.raises(DiscoveryFailure) as caught:
            await collect_models(config, policy, http)
    assert caught.value.code == code
    assert "secret-key" not in str(caught.value)
