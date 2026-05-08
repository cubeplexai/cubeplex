"""Unit tests for cubebox.mcp.oauth.metadata."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from cubebox.mcp.exceptions import OAuthMetadataFetchError, OAuthMetadataNotFound
from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery

Handler = Callable[[httpx.Request], httpx.Response]


def _client_with(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class _CountingHandler:
    """httpx MockTransport handler that records the URLs it was asked for."""

    def __init__(self, responses: dict[str, httpx.Response]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if url not in self._responses:
            return httpx.Response(404, json={"error": "not_found"})
        return self._responses[url]


async def test_fetch_protected_resource_happy_path() -> None:
    handler = _CountingHandler(
        {
            "https://mcp.example.com/.well-known/oauth-protected-resource": httpx.Response(
                200,
                json={
                    "resource": "https://mcp.example.com/",
                    "authorization_servers": ["https://auth.example.com"],
                },
            ),
        }
    )
    async with _client_with(handler) as http:
        discovery = OAuthMetadataDiscovery(http, cache_ttl_seconds=3600)
        pr = await discovery.fetch_protected_resource("https://mcp.example.com")
    assert pr.resource == "https://mcp.example.com/"
    assert pr.authorization_servers == ["https://auth.example.com"]


async def test_fetch_protected_resource_missing_field_raises_not_found() -> None:
    handler = _CountingHandler(
        {
            "https://mcp.example.com/.well-known/oauth-protected-resource": httpx.Response(
                200,
                json={"resource": "https://mcp.example.com/"},
            ),
        }
    )
    async with _client_with(handler) as http:
        discovery = OAuthMetadataDiscovery(http)
        with pytest.raises(OAuthMetadataNotFound):
            await discovery.fetch_protected_resource("https://mcp.example.com")


async def test_fetch_protected_resource_404_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    async with _client_with(handler) as http:
        discovery = OAuthMetadataDiscovery(http)
        with pytest.raises(OAuthMetadataNotFound):
            await discovery.fetch_protected_resource("https://mcp.example.com")


async def test_fetch_protected_resource_500_raises_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    async with _client_with(handler) as http:
        discovery = OAuthMetadataDiscovery(http)
        with pytest.raises(OAuthMetadataFetchError) as excinfo:
            await discovery.fetch_protected_resource("https://mcp.example.com")
    assert excinfo.value.status == 503


async def test_fetch_authorization_server_full_payload() -> None:
    body: dict[str, Any] = {
        "issuer": "https://auth.example.com",
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token",
        "revocation_endpoint": "https://auth.example.com/revoke",
        "registration_endpoint": "https://auth.example.com/register",
        "code_challenge_methods_supported": ["S256"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "response_types_supported": ["code"],
        "scopes_supported": ["read", "write"],
    }
    handler = _CountingHandler(
        {
            "https://auth.example.com/.well-known/oauth-authorization-server": httpx.Response(
                200, json=body
            ),
        }
    )
    async with _client_with(handler) as http:
        discovery = OAuthMetadataDiscovery(http)
        meta = await discovery.fetch_authorization_server("https://auth.example.com")
    assert meta.issuer == "https://auth.example.com"
    assert meta.authorization_endpoint == "https://auth.example.com/authorize"
    assert meta.token_endpoint == "https://auth.example.com/token"
    assert meta.revocation_endpoint == "https://auth.example.com/revoke"
    assert meta.registration_endpoint == "https://auth.example.com/register"
    assert meta.code_challenge_methods_supported == ["S256"]
    assert meta.scopes_supported == ["read", "write"]
    assert meta.raw == body


async def test_fetch_authorization_server_minimal_payload() -> None:
    body = {
        "issuer": "https://auth.example.com",
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token",
    }
    handler = _CountingHandler(
        {
            "https://auth.example.com/.well-known/oauth-authorization-server": httpx.Response(
                200, json=body
            ),
        }
    )
    async with _client_with(handler) as http:
        discovery = OAuthMetadataDiscovery(http)
        meta = await discovery.fetch_authorization_server("https://auth.example.com")
    assert meta.revocation_endpoint is None
    assert meta.registration_endpoint is None
    assert meta.scopes_supported is None
    assert meta.code_challenge_methods_supported == []
    assert meta.grant_types_supported == []
    assert meta.response_types_supported == []


async def test_fetch_authorization_server_missing_required_raises_not_found() -> None:
    body = {
        "issuer": "https://auth.example.com",
        "authorization_endpoint": "https://auth.example.com/authorize",
        # token_endpoint missing
    }
    handler = _CountingHandler(
        {
            "https://auth.example.com/.well-known/oauth-authorization-server": httpx.Response(
                200, json=body
            ),
        }
    )
    async with _client_with(handler) as http:
        discovery = OAuthMetadataDiscovery(http)
        with pytest.raises(OAuthMetadataNotFound):
            await discovery.fetch_authorization_server("https://auth.example.com")


async def test_metadata_cache_serves_second_call_from_cache() -> None:
    body = {
        "resource": "https://mcp.example.com/",
        "authorization_servers": ["https://auth.example.com"],
    }
    handler = _CountingHandler(
        {
            "https://mcp.example.com/.well-known/oauth-protected-resource": httpx.Response(
                200, json=body
            ),
        }
    )
    async with _client_with(handler) as http:
        discovery = OAuthMetadataDiscovery(http, cache_ttl_seconds=3600)
        first = await discovery.fetch_protected_resource("https://mcp.example.com")
        second = await discovery.fetch_protected_resource("https://mcp.example.com")
    assert first == second
    assert len(handler.calls) == 1


async def test_discover_for_resource_orchestrates_pr_then_as() -> None:
    handler = _CountingHandler(
        {
            "https://mcp.example.com/.well-known/oauth-protected-resource": httpx.Response(
                200,
                json={
                    "resource": "https://mcp.example.com/",
                    "authorization_servers": ["https://auth.example.com"],
                },
            ),
            "https://auth.example.com/.well-known/oauth-authorization-server": httpx.Response(
                200,
                json={
                    "issuer": "https://auth.example.com",
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                    "registration_endpoint": "https://auth.example.com/register",
                },
            ),
        }
    )
    async with _client_with(handler) as http:
        discovery = OAuthMetadataDiscovery(http)
        pr, as_meta = await discovery.discover_for_resource("https://mcp.example.com")
    assert pr.authorization_servers == ["https://auth.example.com"]
    assert as_meta.token_endpoint == "https://auth.example.com/token"
    assert as_meta.registration_endpoint == "https://auth.example.com/register"
