"""Contracts for deployment-controlled MCP outbound requests."""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock

import pytest

from cubeplex.mcp.outbound import MCPOutboundRefused, validate_mcp_outbound_url


def _settings(values: dict[str, object]):
    def get(key: str, default: object = None) -> object:
        return values.get(key, default)

    return get


def _resolver(*addresses: str):
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (address, 443),
        )
        for address in addresses
    ]


@pytest.mark.asyncio
async def test_public_and_allowlist_allows_public_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cubeplex.mcp.outbound.config.get",
        _settings({"mcp.outbound_policy": "public_and_allowlist"}),
    )
    monkeypatch.setattr(
        "cubeplex.mcp.outbound.anyio.getaddrinfo", AsyncMock(return_value=_resolver("8.8.8.8"))
    )

    await validate_mcp_outbound_url("https://mcp.example.com/tools")


@pytest.mark.asyncio
async def test_public_and_allowlist_rejects_private_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cubeplex.mcp.outbound.config.get",
        _settings({"mcp.outbound_policy": "public_and_allowlist"}),
    )
    monkeypatch.setattr(
        "cubeplex.mcp.outbound.anyio.getaddrinfo", AsyncMock(return_value=_resolver("10.0.0.8"))
    )

    with pytest.raises(MCPOutboundRefused, match="destination_not_allowed"):
        await validate_mcp_outbound_url("https://mcp.internal/tools")


@pytest.mark.asyncio
async def test_allowlisted_cidr_permits_private_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cubeplex.mcp.outbound.config.get",
        _settings(
            {
                "mcp.outbound_policy": "public_and_allowlist",
                "mcp.allowed_cidrs": ["10.20.0.0/16"],
            }
        ),
    )
    monkeypatch.setattr(
        "cubeplex.mcp.outbound.anyio.getaddrinfo", AsyncMock(return_value=_resolver("10.20.1.4"))
    )

    await validate_mcp_outbound_url("https://mcp.internal/tools")


@pytest.mark.asyncio
async def test_allowlisted_host_permits_private_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cubeplex.mcp.outbound.config.get",
        _settings(
            {
                "mcp.outbound_policy": "public_and_allowlist",
                "mcp.allowed_hosts": ["mcp.internal"],
            }
        ),
    )
    monkeypatch.setattr(
        "cubeplex.mcp.outbound.anyio.getaddrinfo", AsyncMock(return_value=_resolver("127.0.0.1"))
    )

    await validate_mcp_outbound_url("https://mcp.internal/tools")


@pytest.mark.asyncio
async def test_default_scheme_rejects_plain_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cubeplex.mcp.outbound.config.get",
        _settings({"mcp.outbound_policy": "public_and_allowlist"}),
    )

    with pytest.raises(MCPOutboundRefused, match="scheme_not_allowed"):
        await validate_mcp_outbound_url("http://mcp.example.com/tools")


@pytest.mark.asyncio
async def test_allowlist_only_rejects_public_destination_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cubeplex.mcp.outbound.config.get",
        _settings({"mcp.outbound_policy": "allowlist_only"}),
    )
    monkeypatch.setattr(
        "cubeplex.mcp.outbound.anyio.getaddrinfo",
        AsyncMock(return_value=_resolver("8.8.8.8")),
    )

    with pytest.raises(MCPOutboundRefused, match="destination_not_allowed"):
        await validate_mcp_outbound_url("https://mcp.example.com/tools")
