"""Bounded HTTP collection from an explicitly configured model endpoint."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from collections.abc import Iterable
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from cubeplex.llm.discovery_types import CallingConfiguration, DiscoveryPolicy

MAX_BYTES = 5 * 1024 * 1024
MAX_MODELS = 2000
MAX_PAGES = 20


class DiscoveryFailure(Exception):
    def __init__(self, code: str, message: str, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


def validate_address(address: str) -> None:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if (
        ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or str(ip) in {"100.100.100.200", "fd00:ec2::254"}
    ):
        raise DiscoveryFailure("blocked_destination", "This endpoint address is not allowed.")


def validated_base(base_url: str) -> str:
    parts = urlsplit(base_url.strip())
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or parts.query
        or any(segment in {".", ".."} for segment in parts.path.split("/"))
    ):
        raise DiscoveryFailure("invalid_endpoint", "Enter an HTTP(S) API base URL.")
    try:
        _port = parts.port
        ipaddress.ip_address(parts.hostname)
    except ValueError as exc:
        if ":" in parts.hostname or "port" in str(exc).lower():
            raise DiscoveryFailure("invalid_endpoint", "Invalid endpoint address.") from exc
    else:
        validate_address(parts.hostname)
    path = parts.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/messages", "/models"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def instance_base(base_url: str) -> str:
    base = validated_base(base_url)
    parts = urlsplit(base)
    path = parts.path
    if path.endswith("/v1"):
        path = path[:-3]
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def discovery_url(base_url: str, policy: DiscoveryPolicy) -> str:
    base = validated_base(base_url)
    parts = urlsplit(base)
    if policy.url_mode == "origin":
        base = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    elif policy.url_mode == "instance":
        base = instance_base(base)
    elif not parts.path:
        base += policy.root_api_path
    return base.rstrip("/") + "/" + policy.path.lstrip("/")


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self._backend = AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        async with asyncio.timeout(timeout):
            addresses = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
            if not addresses:
                raise DiscoveryFailure("transport_error", "Could not resolve this endpoint.")
            ips = list(dict.fromkeys(str(address[4][0]) for address in addresses))
            for ip in ips:
                validate_address(ip)
            # Connect to validated IPs; HTTP Host and TLS SNI retain the original host.
            for index, ip in enumerate(ips):
                try:
                    return await self._backend.connect_tcp(
                        ip, port, timeout, local_address, socket_options
                    )
                except httpcore.ConnectError:
                    if index == len(ips) - 1:
                        raise
        raise DiscoveryFailure("transport_error", "Could not connect to this endpoint.")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise DiscoveryFailure("blocked_destination", "Unix sockets are not discovery endpoints.")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class _PinnedTransport(httpx.AsyncHTTPTransport):
    def __init__(self) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(network_backend=_PinnedBackend())


class DiscoveryTransport:
    def __init__(
        self,
        config: CallingConfiguration,
        policy: DiscoveryPolicy,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.policy = policy
        self._base = validated_base(config.base_url)
        self._deadline = time.monotonic() + 25
        self._bytes = 0
        self._requests = 0
        self._client = httpx.AsyncClient(
            transport=transport or _PinnedTransport(),
            timeout=httpx.Timeout(15, connect=5),
            follow_redirects=False,
            trust_env=False,
        )

    async def __aenter__(self) -> DiscoveryTransport:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._client.aclose()

    def headers(self) -> dict[str, str]:
        headers = {
            name: value
            for name, value in self.config.extra_headers.items()
            if name.lower() not in {"authorization", "x-api-key", "host", "content-length"}
        }
        secret = self.config.api_key
        if self.config.auth_type != "none" and secret:
            if self.policy.auth == "anthropic":
                headers["x-api-key"] = secret
                headers["anthropic-version"] = "2023-06-01"
            elif self.policy.auth == "bearer":
                headers["Authorization"] = f"Bearer {secret}"
        return headers

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        original = urlsplit(self._base)
        target = urlsplit(url)
        if (target.scheme, target.netloc) != (original.scheme, original.netloc):
            raise DiscoveryFailure("blocked_destination", "Discovery cannot change origin.")
        remaining = self._deadline - time.monotonic()
        self._requests += 1
        if remaining <= 0 or self._requests > MAX_MODELS + MAX_PAGES:
            raise DiscoveryFailure("discovery_limit", "Discovery exceeded its collection limit.")
        try:
            async with asyncio.timeout(remaining):
                async with self._client.stream(
                    method,
                    url,
                    params=params,
                    json=body,
                    headers=self.headers(),
                ) as response:
                    code = response.status_code
                    if code in {401, 403}:
                        raise DiscoveryFailure(
                            "authentication_error", "The endpoint rejected the key."
                        )
                    if code == 429:
                        retry = response.headers.get("Retry-After", "")[:64] or None
                        raise DiscoveryFailure(
                            "rate_limited", "The endpoint rate limited discovery.", retry
                        )
                    if code in {404, 405, 501}:
                        raise DiscoveryFailure(
                            "not_supported", "This endpoint does not support listing."
                        )
                    if not 200 <= code < 300:
                        raise DiscoveryFailure(
                            "upstream_error", "The endpoint could not return a listing."
                        )
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        self._bytes += len(chunk)
                        if self._bytes > MAX_BYTES:
                            raise DiscoveryFailure(
                                "discovery_limit", "Discovery response is too large."
                            )
                        chunks.extend(chunk)
                    payload = json.loads(chunks)
                    if not isinstance(payload, dict):
                        raise DiscoveryFailure(
                            "malformed_response", "Expected a model-list object."
                        )
                    return cast(dict[str, Any], payload)
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise DiscoveryFailure("discovery_timeout", "Discovery timed out.") from exc
        except (httpx.HTTPError, OSError) as exc:
            raise DiscoveryFailure(
                "transport_error", "Could not connect to this endpoint."
            ) from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise DiscoveryFailure(
                "malformed_response", "The endpoint returned invalid JSON."
            ) from exc
