"""DoclingParser HTTP client tests (httpx MockTransport)."""

import json

import httpx

from cubeplex.parsers.plugins.docling import DoclingParser
from cubeplex.parsers.protocols import FileParser
from cubeplex.parsers.schema import ErrorOutput, ParseOptions, TextOutput


def test_satisfies_protocol() -> None:
    assert isinstance(DoclingParser(base_url="http://test"), FileParser)


async def test_sync_path_for_small_file() -> None:
    """File < async_threshold_mb hits sync /v1/convert/source endpoint."""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/convert/source"
        body = json.loads(request.content)
        assert body["sources"][0]["kind"] == "file"
        # docling-serve's FileSourceRequest wants base64_string (flat, not nested).
        assert "base64_string" in body["sources"][0]
        assert body["options"]["image_export_mode"] == "placeholder"
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


async def test_async_path_for_large_file() -> None:
    """File >= async_threshold_mb hits async submit + poll + result-fetch."""
    poll_count = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/convert/source/async":
            body = json.loads(request.content)
            assert body["options"]["image_export_mode"] == "placeholder"
            return httpx.Response(200, json={"task_id": "tk_123", "task_status": "pending"})
        if path == "/v1/status/poll/tk_123":
            poll_count["n"] += 1
            if poll_count["n"] < 2:
                return httpx.Response(200, json={"task_status": "started"})
            return httpx.Response(200, json={"task_status": "success"})
        if path == "/v1/result/tk_123":
            return httpx.Response(200, json={"document": {"md_content": "# Big\n\ndata"}})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    p = DoclingParser(
        base_url="http://docling",
        api_key=None,
        timeout_sync_seconds=30,
        timeout_async_minutes=10,
        async_threshold_mb=1,
        poll_interval_seconds=0,  # tests should not sleep
        _transport=transport,
    )
    big = b"x" * (2 * 1024 * 1024)  # 2 MB > 1 MB threshold
    out = await p.parse(big, mime="application/pdf", options=ParseOptions())
    assert isinstance(out, TextOutput)
    assert "Big" in out.content


async def test_async_path_task_failed_returns_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/convert/source/async":
            return httpx.Response(200, json={"task_id": "tk_x", "task_status": "pending"})
        if path == "/v1/status/poll/tk_x":
            return httpx.Response(
                200, json={"task_status": "failure", "error_message": "corrupt input"}
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    p = DoclingParser(
        base_url="http://docling",
        api_key=None,
        timeout_sync_seconds=30,
        timeout_async_minutes=10,
        async_threshold_mb=1,
        poll_interval_seconds=0,
        _transport=transport,
    )
    big = b"x" * (2 * 1024 * 1024)
    out = await p.parse(big, mime="application/pdf", options=ParseOptions())
    assert isinstance(out, ErrorOutput)
    assert out.retryable is False
    assert "corrupt" in out.error
