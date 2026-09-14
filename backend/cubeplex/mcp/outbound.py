"""Deployment policy for backend requests made on behalf of MCP configuration."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlparse

import anyio

from cubeplex.config import config


class MCPOutboundRefused(ValueError):
    """The configured MCP outbound policy does not permit a target URL."""


_VALID_POLICIES = frozenset(
    {"disabled", "public_only", "allowlist_only", "public_and_allowlist", "unrestricted"}
)


async def validate_mcp_outbound_url(url: str) -> None:
    """Reject MCP targets outside the deployment's outbound policy."""
    policy = _string_setting("mcp.outbound_policy", "public_and_allowlist")
    if policy not in _VALID_POLICIES:
        raise MCPOutboundRefused("invalid_outbound_policy")
    if policy == "disabled":
        raise MCPOutboundRefused("outbound_disabled")

    parsed = urlparse(url)
    allowed_schemes = _string_settings("mcp.allowed_schemes", ("https",))
    if parsed.scheme.lower() not in allowed_schemes:
        raise MCPOutboundRefused("scheme_not_allowed")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise MCPOutboundRefused("missing_host")
    if policy == "unrestricted":
        return

    allowed_hosts = set(_string_settings("mcp.allowed_hosts", ()))
    allowed_cidrs = _networks(_string_settings("mcp.allowed_cidrs", ()))
    host_allowed = host in allowed_hosts
    try:
        infos = await anyio.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except (OSError, ValueError) as exc:
        raise MCPOutboundRefused("dns_lookup_failed") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        allowed_by_list = policy in {"allowlist_only", "public_and_allowlist"} and (
            host_allowed or any(address in network for network in allowed_cidrs)
        )
        allowed_publicly = address.is_global and policy in {"public_only", "public_and_allowlist"}
        if not allowed_by_list and not allowed_publicly:
            raise MCPOutboundRefused("destination_not_allowed")


def _string_setting(key: str, default: str) -> str:
    value = config.get(key, default)
    return value.lower() if isinstance(value, str) else default


def _string_settings(key: str, default: Iterable[str]) -> tuple[str, ...]:
    value = config.get(key, list(default))
    if not isinstance(value, (list, tuple, set)):
        return tuple(item.lower() for item in default)
    return tuple(str(item).rstrip(".").lower() for item in value if isinstance(item, str))


def _networks(values: Iterable[str]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for value in values:
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise MCPOutboundRefused("invalid_allowed_cidr") from exc
    return tuple(networks)
