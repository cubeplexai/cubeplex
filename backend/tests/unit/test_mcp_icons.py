"""Unit tests for cubebox.mcp.icons — SSRF guard + materialisation."""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
import respx

from cubebox.mcp.icons import (
    IconFetchRefused,
    enrich_server_icons,
    fetch_icon_as_data_uri,
    refuse_ssrf_icon_url,
    server_icons_from_discovery,
    template_icon_key,
    to_data_uri,
)


def test_to_data_uri_roundtrip() -> None:
    body = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
    uri = to_data_uri(body, "image/svg+xml")
    assert uri.startswith("data:image/svg+xml;base64,")
    decoded = base64.b64decode(uri.split(",", 1)[1])
    assert decoded == body


def test_refuse_ssrf_rejects_http_and_private() -> None:
    with pytest.raises(IconFetchRefused, match="scheme_must_be_https"):
        refuse_ssrf_icon_url("http://example.com/logo.svg")
    with pytest.raises(IconFetchRefused, match="private_address_blocked"):
        refuse_ssrf_icon_url("https://127.0.0.1/logo.svg")
    with pytest.raises(IconFetchRefused, match="private_address_blocked"):
        refuse_ssrf_icon_url("https://10.0.0.5/logo.svg")


def test_template_icon_key_validates_slug() -> None:
    assert template_icon_key({"icon": "linear"}) == "linear"
    assert template_icon_key({"icon": "cloudflare-api"}) == "cloudflare-api"
    assert template_icon_key({"icon": "../etc/passwd"}) is None
    assert template_icon_key({"icon": "a/b"}) is None
    assert template_icon_key({}) is None
    assert template_icon_key(None) is None


def test_server_icons_from_discovery() -> None:
    meta = {
        "server": {
            "icons": [
                {"src": "https://example.com/a.svg"},
                {"src": ""},
                "not-a-dict",
            ]
        }
    }
    assert server_icons_from_discovery(meta) == [{"src": "https://example.com/a.svg"}]
    assert server_icons_from_discovery({}) == []
    assert server_icons_from_discovery(None) == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_icon_as_data_uri_success(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bypass DNS SSRF check for a public hostname mock — respx intercepts
    # the HTTP call, but refuse_ssrf still resolves DNS. Patch the guard
    # for the known-good public host used below.
    monkeypatch.setattr(
        "cubebox.mcp.icons.refuse_ssrf_icon_url",
        lambda url: None,
    )
    body = b"<svg/>"
    respx.get("https://icons.example.com/logo.svg").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "image/svg+xml"})
    )
    uri = await fetch_icon_as_data_uri("https://icons.example.com/logo.svg")
    assert uri == to_data_uri(body, "image/svg+xml")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_icon_rejects_non_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cubebox.mcp.icons.refuse_ssrf_icon_url", lambda url: None)
    respx.get("https://icons.example.com/not-image").mock(
        return_value=httpx.Response(
            200, content=b"<html>nope</html>", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(ValueError, match="unsupported_content_type"):
        await fetch_icon_as_data_uri("https://icons.example.com/not-image")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_icon_rejects_oversized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cubebox.mcp.icons.refuse_ssrf_icon_url", lambda url: None)
    respx.get("https://icons.example.com/big.svg").mock(
        return_value=httpx.Response(
            200,
            content=b"x" * 1000,
            headers={"content-type": "image/svg+xml"},
        )
    )
    with pytest.raises(ValueError, match="icon_exceeds_max_bytes"):
        await fetch_icon_as_data_uri(
            "https://icons.example.com/big.svg",
            max_bytes=100,
        )


@pytest.mark.asyncio
@respx.mock
async def test_enrich_server_icons_adds_cached_src(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cubebox.mcp.icons.refuse_ssrf_icon_url", lambda url: None)
    monkeypatch.setattr("cubebox.mcp.icons.icons_fetch_remote_enabled", lambda: True)
    body = b"<svg id='x'/>"
    respx.get("https://icons.example.com/logo.svg").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "image/svg+xml"})
    )
    icons: list[dict[str, Any]] = [
        {"src": "https://icons.example.com/logo.svg", "mime_type": "image/svg+xml"},
        {"src": "data:image/svg+xml;base64,abc", "mime_type": "image/svg+xml"},
        {"src": "/mcp-icons/local.svg"},
    ]
    out = await enrich_server_icons(icons)
    assert out[0]["src"] == "https://icons.example.com/logo.svg"
    assert out[0]["cached_src"] == to_data_uri(body, "image/svg+xml")
    assert "cached_src" not in out[1]  # already data:
    assert "cached_src" not in out[2]  # relative


@pytest.mark.asyncio
@respx.mock
async def test_enrich_server_icons_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cubebox.mcp.icons.refuse_ssrf_icon_url", lambda url: None)
    monkeypatch.setattr("cubebox.mcp.icons.icons_fetch_remote_enabled", lambda: True)
    respx.get("https://icons.example.com/missing.svg").mock(return_value=httpx.Response(404))
    out = await enrich_server_icons(
        [{"src": "https://icons.example.com/missing.svg", "mime_type": "image/svg+xml"}]
    )
    assert out[0]["src"] == "https://icons.example.com/missing.svg"
    assert "cached_src" not in out[0]


@pytest.mark.asyncio
async def test_enrich_skips_fetch_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cubebox.mcp.icons.icons_fetch_remote_enabled", lambda: False)
    out = await enrich_server_icons([{"src": "https://icons.example.com/logo.svg"}])
    assert out == [{"src": "https://icons.example.com/logo.svg"}]
    assert "cached_src" not in out[0]
