"""Unit tests for cubeplex.mcp.oauth.dcr."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cubeplex.mcp.exceptions import DCRError
from cubeplex.mcp.oauth.dcr import (
    DEFAULT_GRANT_TYPES,
    DEFAULT_RESPONSE_TYPES,
    DEFAULT_TOKEN_AUTH_METHOD,
    DCRClient,
    DCRRequest,
)

REGISTRATION_ENDPOINT = "https://auth.example.com/register"


class _RecordingHandler:
    """MockTransport handler that records the request body and returns a canned response."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.last_body: dict[str, Any] | None = None
        self.last_method: str | None = None
        self.last_url: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.last_method = request.method
        self.last_url = str(request.url)
        body_bytes = request.read()
        self.last_body = json.loads(body_bytes) if body_bytes else None
        return self._response


def _client(handler: _RecordingHandler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_register_happy_path_parses_response() -> None:
    handler = _RecordingHandler(
        httpx.Response(
            201,
            json={
                "client_id": "cid-abc",
                "client_secret": "shh",
                "client_id_issued_at": 1700000000,
                "client_secret_expires_at": 0,
            },
        )
    )
    async with _client(handler) as http:
        client = DCRClient(http)
        result = await client.register(
            REGISTRATION_ENDPOINT,
            DCRRequest(
                redirect_uris=["https://app.example.com/cb"],
                client_name="cubeplex",
            ),
        )
    assert result.client_id == "cid-abc"
    assert result.client_secret == "shh"
    assert result.client_id_issued_at == 1700000000
    assert result.client_secret_expires_at == 0
    assert result.raw["client_id"] == "cid-abc"


async def test_register_sends_default_grant_response_and_auth_method() -> None:
    handler = _RecordingHandler(httpx.Response(201, json={"client_id": "cid"}))
    async with _client(handler) as http:
        client = DCRClient(http)
        await client.register(
            REGISTRATION_ENDPOINT,
            DCRRequest(
                redirect_uris=["https://app.example.com/cb"],
                client_name="cubeplex",
            ),
        )
    assert handler.last_method == "POST"
    assert handler.last_url == REGISTRATION_ENDPOINT
    body = handler.last_body
    assert body is not None
    assert body["redirect_uris"] == ["https://app.example.com/cb"]
    assert body["client_name"] == "cubeplex"
    assert body["grant_types"] == DEFAULT_GRANT_TYPES
    assert body["response_types"] == DEFAULT_RESPONSE_TYPES
    assert body["token_endpoint_auth_method"] == DEFAULT_TOKEN_AUTH_METHOD
    # MCP cubeplex is a public client using PKCE → default auth method must be "none"
    assert DEFAULT_TOKEN_AUTH_METHOD == "none"
    assert body["token_endpoint_auth_method"] == "none"
    assert "scope" not in body  # not provided => not sent


async def test_register_explicit_auth_method_override_flows_through() -> None:
    """An explicit token_endpoint_auth_method is forwarded; default is just the default."""
    handler = _RecordingHandler(httpx.Response(201, json={"client_id": "cid"}))
    async with _client(handler) as http:
        client = DCRClient(http)
        await client.register(
            REGISTRATION_ENDPOINT,
            DCRRequest(
                redirect_uris=["https://app.example.com/cb"],
                client_name="cubeplex",
                token_endpoint_auth_method="client_secret_basic",
            ),
        )
    assert handler.last_body is not None
    assert handler.last_body["token_endpoint_auth_method"] == "client_secret_basic"


async def test_register_accepts_200_ok_in_addition_to_201() -> None:
    """RFC 7591 says 201 Created, but Keycloak / older Auth0 return 200 OK."""
    handler = _RecordingHandler(
        httpx.Response(
            200,
            json={
                "client_id": "cid-200",
                "client_secret": None,
            },
        )
    )
    async with _client(handler) as http:
        client = DCRClient(http)
        result = await client.register(
            REGISTRATION_ENDPOINT,
            DCRRequest(
                redirect_uris=["https://app.example.com/cb"],
                client_name="cubeplex",
            ),
        )
    assert result.client_id == "cid-200"
    assert result.client_secret is None


async def test_register_includes_scope_when_provided() -> None:
    handler = _RecordingHandler(httpx.Response(201, json={"client_id": "cid"}))
    async with _client(handler) as http:
        client = DCRClient(http)
        await client.register(
            REGISTRATION_ENDPOINT,
            DCRRequest(
                redirect_uris=["https://app.example.com/cb"],
                client_name="cubeplex",
                scope="read write",
            ),
        )
    assert handler.last_body is not None
    assert handler.last_body["scope"] == "read write"


async def test_register_400_with_error_body_raises_dcr_error() -> None:
    handler = _RecordingHandler(
        httpx.Response(
            400,
            json={
                "error": "invalid_redirect_uri",
                "error_description": "redirect_uri scheme must be https",
            },
        )
    )
    async with _client(handler) as http:
        client = DCRClient(http)
        with pytest.raises(DCRError) as excinfo:
            await client.register(
                REGISTRATION_ENDPOINT,
                DCRRequest(
                    redirect_uris=["http://app.example.com/cb"],
                    client_name="cubeplex",
                ),
            )
    err = excinfo.value
    assert err.status == 400
    assert err.error == "invalid_redirect_uri"
    assert err.error_description == "redirect_uri scheme must be https"


async def test_register_500_without_body_raises_dcr_error() -> None:
    handler = _RecordingHandler(httpx.Response(500, text="boom"))
    async with _client(handler) as http:
        client = DCRClient(http)
        with pytest.raises(DCRError) as excinfo:
            await client.register(
                REGISTRATION_ENDPOINT,
                DCRRequest(
                    redirect_uris=["https://app.example.com/cb"],
                    client_name="cubeplex",
                ),
            )
    assert excinfo.value.status == 500


async def test_register_201_missing_client_id_raises_dcr_error() -> None:
    handler = _RecordingHandler(httpx.Response(201, json={"unexpected": "shape"}))
    async with _client(handler) as http:
        client = DCRClient(http)
        with pytest.raises(DCRError):
            await client.register(
                REGISTRATION_ENDPOINT,
                DCRRequest(
                    redirect_uris=["https://app.example.com/cb"],
                    client_name="cubeplex",
                ),
            )
