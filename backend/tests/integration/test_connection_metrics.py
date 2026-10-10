"""Connection gauges describe live streams, including disconnect cleanup."""

import asyncio

import pytest
from starlette.types import Message, Receive, Scope, Send

from cubeplex.metrics import (
    HTTP_REQUESTS_IN_FLIGHT,
    SSE_CONNECTIONS_ACTIVE,
    SSE_CONNECTIONS_OPENED,
    ConnectionMetricsMiddleware,
)


@pytest.mark.asyncio
async def test_sse_gauge_tracks_the_open_connection_until_it_closes() -> None:
    opened = asyncio.Event()
    release = asyncio.Event()

    async def stream(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream; charset=utf-8")],
            }
        )
        opened.set()
        await release.wait()
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(_message: Message) -> None:
        return None

    scope: Scope = {"type": "http", "path": "/api/v1/stream"}  # type: ignore[typeddict-item]
    middleware = ConnectionMetricsMiddleware(stream)
    initial_active = SSE_CONNECTIONS_ACTIVE._value.get()
    initial_opened = SSE_CONNECTIONS_OPENED._value.get()
    initial_http = HTTP_REQUESTS_IN_FLIGHT._value.get()

    task = asyncio.create_task(middleware(scope, receive, send))
    try:
        await asyncio.wait_for(opened.wait(), timeout=1)
        assert SSE_CONNECTIONS_ACTIVE._value.get() == initial_active + 1
        assert SSE_CONNECTIONS_OPENED._value.get() == initial_opened + 1
        assert HTTP_REQUESTS_IN_FLIGHT._value.get() == initial_http + 1
    finally:
        release.set()
        await task

    assert SSE_CONNECTIONS_ACTIVE._value.get() == initial_active
    assert HTTP_REQUESTS_IN_FLIGHT._value.get() == initial_http
