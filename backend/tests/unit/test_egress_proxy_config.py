"""Unit tests for the proxy-config endpoint logic.

Tests the sandbox_id → org_id → policy resolution chain, not HTTP/mTLS.
"""

from unittest.mock import AsyncMock, patch

import pytest

from cubeplex.api.routes.internal_egress import _resolve_proxy_for_sandbox


@pytest.mark.asyncio
async def test_resolve_proxy_returns_configured_proxy():
    """sandbox_id → UserSandbox → (org_id, provider) → SandboxPolicy.egress_proxy."""
    session = AsyncMock()
    with (
        patch(
            "cubeplex.api.routes.internal_egress._lookup_org_and_provider_by_sandbox",
            new_callable=AsyncMock,
            return_value=("org_123", "local"),
        ),
        patch(
            "cubeplex.api.routes.internal_egress._resolve_egress_proxy_for_org",
            new_callable=AsyncMock,
            return_value="http://192.168.1.150:7892",
        ) as mock_resolve,
    ):
        result = await _resolve_proxy_for_sandbox(session, sandbox_id="sbx-1")
    assert result == "http://192.168.1.150:7892"
    mock_resolve.assert_awaited_once()
    assert mock_resolve.call_args.kwargs["provider"].name == "local"


@pytest.mark.asyncio
async def test_resolve_proxy_returns_none_when_no_policy():
    session = AsyncMock()
    with (
        patch(
            "cubeplex.api.routes.internal_egress._lookup_org_and_provider_by_sandbox",
            new_callable=AsyncMock,
            return_value=("org_123", "opensandbox"),
        ),
        patch(
            "cubeplex.api.routes.internal_egress._resolve_egress_proxy_for_org",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await _resolve_proxy_for_sandbox(session, sandbox_id="sbx-1")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_proxy_returns_none_when_sandbox_unknown():
    session = AsyncMock()
    with patch(
        "cubeplex.api.routes.internal_egress._lookup_org_and_provider_by_sandbox",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await _resolve_proxy_for_sandbox(session, sandbox_id="sbx-unknown")
    assert result is None
