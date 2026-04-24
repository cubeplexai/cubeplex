"""DoclingParser HTTP client tests (httpx MockTransport)."""

import json

import httpx

from cubebox.parsers.plugins.docling import DoclingParser
from cubebox.parsers.protocols import FileParser
from cubebox.parsers.schema import ErrorOutput, ParseOptions, TextOutput


def test_satisfies_protocol() -> None:
    assert isinstance(DoclingParser(base_url="http://test"), FileParser)


async def test_sync_path_for_small_file() -> None:
    """File < async_threshold_mb hits sync /v1/convert/source endpoint."""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/convert/source"
        body = json.loads(request.content)
        assert body["sources"][0]["kind"] == "file"
        return httpx.Response(
            200,
            json={"document": {"md_content": "# Parsed\n\nhello"}},
        )

    transport = httpx.MockTransport(handler)
    p = DoclingParser(
        base_url="http://docling",
        api_key=None,
        timeout_sync_seconds=30,
        timeout_async_minutes=10,
        async_threshold_mb=3,
        poll_interval_seconds=2,
        _transport=transport,
    )
    content = b"%PDF-1.4 stub"
    out = await p.parse(content, mime="application/pdf", options=ParseOptions())
    assert isinstance(out, TextOutput)
    assert "Parsed" in out.content
    assert out.metadata["parser"] == "docling"


async def test_sync_path_returns_error_on_5xx() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    transport = httpx.MockTransport(handler)
    p = DoclingParser(
        base_url="http://docling",
        api_key=None,
        timeout_sync_seconds=30,
        timeout_async_minutes=10,
        async_threshold_mb=3,
        poll_interval_seconds=2,
        _transport=transport,
    )
    out = await p.parse(b"x" * 100, mime="application/pdf", options=ParseOptions())
    assert isinstance(out, ErrorOutput)
    assert out.retryable is True


async def test_sync_path_truncates_long_content() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"document": {"md_content": "x" * 30_000}},
        )

    transport = httpx.MockTransport(handler)
    p = DoclingParser(
        base_url="http://docling",
        api_key=None,
        timeout_sync_seconds=30,
        timeout_async_minutes=10,
        async_threshold_mb=3,
        poll_interval_seconds=2,
        _transport=transport,
    )
    out = await p.parse(b"x", mime="application/pdf", options=ParseOptions())
    assert isinstance(out, TextOutput)
    assert out.truncated is True
    assert len(out.content) == 20_000
    # No page markers in this fake response → hint instead of next_page_to_read
    assert "hint" in out.metadata
    assert "page_range" in out.metadata["hint"]


async def test_sync_path_extracts_last_page_from_markers() -> None:
    """When docling output contains <!-- page N --> markers, metadata exposes them."""
    md = (
        "intro\n<!-- page 1 -->\n"
        + "filler\n" * 1000
        + "<!-- page 7 -->\nmore content\n"
        + "filler\n" * 3000
        + "<!-- page 12 -->\nlate stuff"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"document": {"md_content": md}})

    transport = httpx.MockTransport(handler)
    p = DoclingParser(
        base_url="http://docling",
        api_key=None,
        timeout_sync_seconds=30,
        timeout_async_minutes=10,
        async_threshold_mb=3,
        poll_interval_seconds=2,
        _transport=transport,
    )
    out = await p.parse(b"x", mime="application/pdf", options=ParseOptions())
    assert isinstance(out, TextOutput)
    assert out.truncated is True
    assert "last_page_returned" in out.metadata
    assert "next_page_to_read" in out.metadata
    assert out.metadata["next_page_to_read"] == out.metadata["last_page_returned"] + 1
