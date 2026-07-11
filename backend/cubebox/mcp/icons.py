"""MCP connector icon helpers.

Handles two concerns:

1. **Discovery-time materialisation** of remote ``https`` icons into an
   offline-safe ``cached_src`` (``data:`` URI). Fail-open: a fetch
   failure never fails discovery; the original ``src`` is kept so the
   browser can still try for online clients.

2. **Config knobs** for air-gapped deployments that want to skip
   outbound icon fetches entirely.

Server icons only are materialised (tool icon lists can be large and
are rendered lazily in the chat UI via the original ``src`` +
``onError`` fallback).
"""

from __future__ import annotations

import base64
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger

from cubebox.config import config

# Image content-types we accept when materialising remote icons.
_ALLOWED_IMAGE_PREFIXES = ("image/",)
_ALLOWED_IMAGE_EXACT = frozenset(
    {"image/svg+xml", "image/png", "image/jpeg", "image/webp", "image/gif"}
)


class IconFetchRefused(Exception):
    """Remote icon URL is not a safe fetch target (SSRF / scheme)."""


def icons_fetch_remote_enabled() -> bool:
    """Whether discovery should attempt outbound icon fetches."""
    return bool(config.get("mcp.icons.fetch_remote", True))


def icons_allow_remote_enabled() -> bool:
    """Whether API/UI should advertise remote ``https`` icon srcs.

    Currently used only as a config surface for operators; the frontend
    also has ``NEXT_PUBLIC_MCP_ALLOW_REMOTE_ICONS``. Kept here so backend
    docs and future server-side filtering share one source of truth.
    """
    return bool(config.get("mcp.icons.allow_remote", True))


def icons_fetch_timeout_seconds() -> float:
    raw = config.get("mcp.icons.fetch_timeout_ms", 2500)
    try:
        return max(0.1, float(raw) / 1000.0)
    except (TypeError, ValueError):
        return 2.5


def icons_max_bytes() -> int:
    try:
        return max(1024, int(config.get("mcp.icons.max_bytes", 262_144)))
    except (TypeError, ValueError):
        return 262_144


def refuse_ssrf_icon_url(url: str) -> None:
    """Reject icon URLs that would let discovery probe the internal network.

    Mirrors the OIDC discovery SSRF guard: require https, resolve DNS,
    refuse private / loopback / link-local / reserved ranges.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise IconFetchRefused("scheme_must_be_https")
    host = parsed.hostname or ""
    if not host:
        raise IconFetchRefused("missing_host")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise IconFetchRefused("dns_lookup_failed") from exc
    for info in infos:
        sockaddr = info[4]
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise IconFetchRefused("private_address_blocked")


def _content_type_ok(content_type: str | None) -> bool:
    if not content_type:
        return False
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime in _ALLOWED_IMAGE_EXACT:
        return True
    # Other image/* (e.g. image/x-icon) — reject non-image entirely.
    return any(mime.startswith(p) for p in _ALLOWED_IMAGE_PREFIXES)


def to_data_uri(body: bytes, content_type: str) -> str:
    mime = content_type.split(";", 1)[0].strip().lower() or "application/octet-stream"
    b64 = base64.b64encode(body).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def fetch_icon_as_data_uri(
    url: str,
    *,
    timeout: float | None = None,
    max_bytes: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Fetch a remote icon and return a ``data:`` URI.

    Redirects are followed manually (max 3 hops) so each hop is
    re-checked against the SSRF guard — automatic redirect following
    would otherwise let an open redirect land on a private IP after the
    initial URL had already been validated.

    Raises :class:`IconFetchRefused` for SSRF / scheme issues and
    ``httpx.HTTPError`` / ``ValueError`` for network or content problems.
    """
    timeout = icons_fetch_timeout_seconds() if timeout is None else timeout
    max_bytes = icons_max_bytes() if max_bytes is None else max_bytes

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        current = url
        for _ in range(4):  # initial + up to 3 redirects
            refuse_ssrf_icon_url(current)
            # Stream so we can abort early on oversized payloads.
            async with client.stream("GET", current) as resp:
                if resp.status_code in {301, 302, 303, 307, 308}:
                    location = resp.headers.get("location")
                    if not location:
                        raise ValueError("redirect_missing_location")
                    current = str(resp.url.join(location))
                    continue
                resp.raise_for_status()
                ctype = resp.headers.get("content-type")
                if not _content_type_ok(ctype):
                    raise ValueError(f"unsupported_content_type:{ctype!r}")
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("icon_exceeds_max_bytes")
                    chunks.append(chunk)
                body = b"".join(chunks)
                if not body:
                    raise ValueError("empty_icon_body")
                assert ctype is not None
                return to_data_uri(body, ctype)
        raise ValueError("too_many_redirects")
    finally:
        if owns_client:
            await client.aclose()


async def enrich_server_icons(icons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a copy of ``icons`` with best-effort ``cached_src`` for https URLs.

    - ``data:`` and relative paths are left unchanged (already offline-safe).
    - ``https://`` icons are fetched when ``mcp.icons.fetch_remote`` is true.
    - Failures are logged and the original entry is kept without ``cached_src``.
    - Never raises for individual icon failures.
    """
    if not icons:
        return []

    fetch_remote = icons_fetch_remote_enabled()
    out: list[dict[str, Any]] = []
    client: httpx.AsyncClient | None = None
    try:
        if fetch_remote and any(
            isinstance(i.get("src"), str) and str(i["src"]).startswith("https://") for i in icons
        ):
            client = httpx.AsyncClient(
                timeout=icons_fetch_timeout_seconds(),
                follow_redirects=False,
            )

        for icon in icons:
            row = dict(icon)
            src = row.get("src")
            if not isinstance(src, str) or not src:
                out.append(row)
                continue
            if src.startswith("data:") or src.startswith("/"):
                out.append(row)
                continue
            if not src.startswith("https://") or not fetch_remote or client is None:
                out.append(row)
                continue
            try:
                row["cached_src"] = await fetch_icon_as_data_uri(src, client=client)
            except Exception as exc:  # noqa: BLE001 — fail-open
                logger.debug(
                    "MCP icon fetch skipped for {}: {}",
                    src[:120],
                    exc,
                )
            out.append(row)
    finally:
        if client is not None:
            await client.aclose()
    return out


def template_icon_key(template_metadata: dict[str, Any] | None) -> str | None:
    """Extract the catalog brand icon key from template_metadata, if any."""
    if not template_metadata:
        return None
    raw = template_metadata.get("icon")
    if isinstance(raw, str) and raw.strip():
        # Only allow simple slug-like keys so the frontend can safely
        # map to /mcp-icons/{key}.svg without path traversal.
        key = raw.strip()
        if all(c.isalnum() or c in "-_" for c in key) and len(key) <= 64:
            return key
    return None


def server_icons_from_discovery(discovery_metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Pull server icons list out of discovery_metadata (may be empty)."""
    if not discovery_metadata:
        return []
    server = discovery_metadata.get("server")
    if not isinstance(server, dict):
        return []
    icons = server.get("icons")
    if not isinstance(icons, list):
        return []
    return [i for i in icons if isinstance(i, dict) and i.get("src")]
