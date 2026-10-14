import pytest

from cubeplex.llm.discovery_transport import discovery_url
from cubeplex.llm.discovery_types import DiscoveryPolicy


@pytest.mark.parametrize(
    "base,mode,path,root,expected",
    [
        (
            "http://localhost:8317/v1/models",
            "relative",
            "models",
            "/v1",
            "http://localhost:8317/v1/models",
        ),
        (
            "https://host/proxy/api/v1/chat/completions",
            "relative",
            "models",
            "",
            "https://host/proxy/api/v1/models",
        ),
        ("https://host/proxy/v1", "instance", "api/tags", "", "https://host/proxy/api/tags"),
        (
            "https://api.deepseek.com/anthropic",
            "origin",
            "models",
            "",
            "https://api.deepseek.com/models",
        ),
        ("http://[::1]:8317", "relative", "models", "/v1", "http://[::1]:8317/v1/models"),
    ],
)
def test_url_construction_preserves_declared_prefix(base, mode, path, root, expected):
    policy = DiscoveryPolicy(path=path, url_mode=mode, root_api_path=root)
    assert discovery_url(base, policy) == expected


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",
        "100.100.100.200",
        "fe80::1",
        "::ffff:169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
    ],
)
def test_metadata_destinations_are_blocked(address):
    from cubeplex.llm.discovery_transport import DiscoveryFailure, validate_address

    with pytest.raises(DiscoveryFailure):
        validate_address(address)


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "192.168.1.25", "10.0.0.4"])
def test_local_admin_endpoints_are_allowed(address):
    from cubeplex.llm.discovery_transport import validate_address

    validate_address(address)


async def test_cross_origin_cannot_receive_the_calling_key():
    import httpx

    from cubeplex.llm.discovery_transport import DiscoveryFailure, DiscoveryTransport
    from cubeplex.llm.discovery_types import CallingConfiguration

    calls = []
    async with DiscoveryTransport(
        CallingConfiguration(base_url="https://original.test/v1", api_key="private-key"),
        DiscoveryPolicy(),
        transport=httpx.MockTransport(lambda req: calls.append(req)),
    ) as http:
        with pytest.raises(DiscoveryFailure, match="origin"):
            await http.request("GET", "https://other.test/v1/models")
    assert not calls
