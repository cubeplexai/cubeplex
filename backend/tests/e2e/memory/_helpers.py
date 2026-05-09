"""SSE consumer helpers for memory E2E tests.

Drives POST /api/v1/ws/{ws}/conversations/{conv}/messages and parses
the Server-Sent Events body. Mirrors the inline pattern in
tests/e2e/test_streaming.py but exposes it as importable functions.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


async def _stream_events(
    client: httpx.AsyncClient,
    ws_id: str,
    conv_id: str,
    content: str,
) -> list[dict[str, Any]]:
    """Send one user message and collect every parsed SSE event."""
    events: list[dict[str, Any]] = []
    async with client.stream(
        "POST",
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json={"content": content},
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


async def send_message_and_collect_text(
    client: httpx.AsyncClient,
    ws_id: str,
    conv_id: str,
    content: str,
) -> str:
    """Drive one turn and concatenate every text_delta payload into the reply."""
    events = await _stream_events(client, ws_id, conv_id, content)
    parts: list[str] = []
    for evt in events:
        if evt.get("type") != "text_delta":
            continue
        data = evt.get("data") or {}
        chunk = data.get("content")
        if isinstance(chunk, str):
            parts.append(chunk)
    return "".join(parts)
