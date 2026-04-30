"""OAuth placeholder — v1 must reject with 409."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.e2e


async def test_oauth_auth_type_rejected(
    admin_client: tuple[AsyncClient, str],
) -> None:
    """Creating a provider with auth_type=oauth returns 409."""
    client, _ws_id = admin_client

    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "oauth-test-e2e",
            "base_url": "https://example.com/api",
            "auth_type": "oauth",
        },
    )
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "provider_oauth_not_implemented"


async def test_auth_type_api_key_requires_key(
    admin_client: tuple[AsyncClient, str],
) -> None:
    """auth_type=api_key without api_key should be rejected."""
    client, _ws_id = admin_client

    res = await client.post(
        "/api/v1/admin/providers",
        json={
            "name": "no-key-test",
            "base_url": "https://example.com/api",
            "auth_type": "api_key",
        },
    )
    assert res.status_code != 201  # should fail validation
