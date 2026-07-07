"""Well-known metadata discovery for MCP OAuth.

- RFC 9728: ``/.well-known/oauth-protected-resource`` (PR metadata)
- RFC 8414: ``/.well-known/oauth-authorization-server`` (AS metadata)

The discovery client wraps an injected ``httpx.AsyncClient`` and keeps a
small in-memory TTL cache keyed by the well-known URL. HTTP errors raise
``OAuthMetadataFetchError``; missing required fields or 404 raise
``OAuthMetadataNotFound``. Network errors propagate as ``httpx.HTTPError``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urlparse, urlunparse

import httpx

from cubebox.mcp.exceptions import OAuthMetadataFetchError, OAuthMetadataNotFound

_PR_WELL_KNOWN = "/.well-known/oauth-protected-resource"
_AS_WELL_KNOWN = "/.well-known/oauth-authorization-server"

_T = TypeVar("_T")


@dataclass(frozen=True)
class ProtectedResourceMetadata:
    """Subset of RFC 9728 protected-resource metadata we rely on."""

    resource: str
    authorization_servers: list[str]


@dataclass(frozen=True)
class AuthorizationServerMetadata:
    """Subset of RFC 8414 authorization-server metadata we rely on."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None
    registration_endpoint: str | None
    code_challenge_methods_supported: list[str]
    grant_types_supported: list[str]
    response_types_supported: list[str]
    scopes_supported: list[str] | None
    raw: dict[str, Any]


class OAuthMetadataDiscovery:
    """Fetch + cache OAuth well-known metadata documents."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        cache_ttl_seconds: int = 3600,
    ) -> None:
        self._http = http_client
        self._cache_ttl = cache_ttl_seconds
        self._pr_cache: dict[str, tuple[float, ProtectedResourceMetadata]] = {}
        self._as_cache: dict[str, tuple[float, AuthorizationServerMetadata]] = {}

    async def discover_for_resource(
        self,
        resource_url: str,
    ) -> tuple[ProtectedResourceMetadata, AuthorizationServerMetadata]:
        """Fetch PR metadata then resolve AS metadata for the first issuer."""
        pr = await self.fetch_protected_resource(resource_url)
        if not pr.authorization_servers:
            raise OAuthMetadataNotFound(
                f"Protected resource at {resource_url} declares no authorization_servers"
            )
        as_meta = await self.fetch_authorization_server(pr.authorization_servers[0])
        return pr, as_meta

    async def fetch_protected_resource(self, base_url: str) -> ProtectedResourceMetadata:
        url = self._join(base_url, _PR_WELL_KNOWN)
        cached = self._cache_get(self._pr_cache, url)
        if cached is not None:
            return cached
        body = await self._get_json(url)
        try:
            resource = str(body["resource"])
            servers_raw = body["authorization_servers"]
        except KeyError as exc:
            raise OAuthMetadataNotFound(
                f"Protected resource metadata at {url} missing required field: {exc.args[0]}"
            ) from exc
        if not isinstance(servers_raw, list) or not servers_raw:
            raise OAuthMetadataNotFound(
                f"Protected resource metadata at {url} has empty authorization_servers"
            )
        authorization_servers = [str(s) for s in servers_raw]
        pr = ProtectedResourceMetadata(
            resource=resource,
            authorization_servers=authorization_servers,
        )
        self._cache_put(self._pr_cache, url, pr)
        return pr

    async def fetch_authorization_server(self, issuer_url: str) -> AuthorizationServerMetadata:
        url = self._join(issuer_url, _AS_WELL_KNOWN)
        return await self.fetch_authorization_server_metadata_url(url)

    async def fetch_authorization_server_metadata_url(
        self,
        metadata_url: str,
    ) -> AuthorizationServerMetadata:
        url = metadata_url
        cached = self._cache_get(self._as_cache, url)
        if cached is not None:
            return cached
        body = await self._get_json(url)
        as_meta = self._parse_authorization_server_metadata(body, url)
        self._cache_put(self._as_cache, url, as_meta)
        return as_meta

    def _parse_authorization_server_metadata(
        self,
        body: dict[str, Any],
        url: str,
    ) -> AuthorizationServerMetadata:
        try:
            issuer = str(body["issuer"])
            authorization_endpoint = str(body["authorization_endpoint"])
            token_endpoint = str(body["token_endpoint"])
        except KeyError as exc:
            raise OAuthMetadataNotFound(
                f"Authorization server metadata at {url} missing required field: {exc.args[0]}"
            ) from exc
        as_meta = AuthorizationServerMetadata(
            issuer=issuer,
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            revocation_endpoint=_opt_str(body.get("revocation_endpoint")),
            registration_endpoint=_opt_str(body.get("registration_endpoint")),
            code_challenge_methods_supported=_opt_str_list(
                body.get("code_challenge_methods_supported")
            )
            or [],
            grant_types_supported=_opt_str_list(body.get("grant_types_supported")) or [],
            response_types_supported=_opt_str_list(body.get("response_types_supported")) or [],
            scopes_supported=_opt_str_list(body.get("scopes_supported")),
            raw=dict(body),
        )
        return as_meta

    async def _get_json(self, url: str) -> dict[str, Any]:
        response = await self._http.get(url)
        if response.status_code == 404:
            raise OAuthMetadataNotFound(f"Metadata not found at {url}")
        if response.status_code >= 400:
            raise OAuthMetadataFetchError(url, response.status_code)
        body = response.json()
        if not isinstance(body, dict):
            raise OAuthMetadataNotFound(f"Metadata at {url} is not a JSON object")
        return body

    @staticmethod
    def _join(base_url: str, path: str) -> str:
        """Construct a .well-known URL per RFC 8414 §3 / RFC 9728 §3.1.

        For an issuer with a path component (e.g. https://auth.example.com/tenant1),
        the well-known suffix is inserted *before* the path, not appended after it.
        """
        parsed = urlparse(base_url.rstrip("/"))
        base_without_path = urlunparse(parsed._replace(path="", query="", fragment=""))
        issuer_path = parsed.path.lstrip("/")
        if issuer_path:
            return f"{base_without_path}{path}/{issuer_path}"
        return f"{base_without_path}{path}"

    def _cache_get(
        self,
        cache: dict[str, tuple[float, _T]],
        key: str,
    ) -> _T | None:
        entry = cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            cache.pop(key, None)
            return None
        return value

    def _cache_put(
        self,
        cache: dict[str, tuple[float, _T]],
        key: str,
        value: _T,
    ) -> None:
        cache[key] = (time.monotonic() + self._cache_ttl, value)


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _opt_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    return [str(v) for v in value]
