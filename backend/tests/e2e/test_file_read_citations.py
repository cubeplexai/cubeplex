"""E2E: file_read tool emits citation events for kind=text results."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _reset_loop_bound_singletons() -> AsyncIterator[None]:
    """Dispose pooled DB connections and reset objectstore/cache singletons."""
    import cubebox.cache as _cache
    import cubebox.objectstore.client as _oc
    from cubebox.db.engine import engine

    _oc._client = None
    _cache.reset_for_tests()
    yield
    await engine.dispose()
    _oc._client = None
    _cache.reset_for_tests()


async def _make_conv(client: httpx.AsyncClient, ws: str) -> str:
    r = await client.post(f"/api/v1/ws/{ws}/conversations", params={"title": "file-cite-test"})
    r.raise_for_status()
    return r.json()["id"]


async def _upload_text(
    client: httpx.AsyncClient,
    ws: str,
    conv: str,
    content: bytes,
    filename: str,
    mime: str = "text/markdown",
) -> str:
    files = {"file": (filename, content, mime)}
    r = await client.post(f"/api/v1/ws/{ws}/conversations/{conv}/attachments", files=files)
    r.raise_for_status()
    return r.json()["id"]


async def _drain_sse_to_done(
    client: httpx.AsyncClient, ws: str, conv: str, body: dict[str, object]
) -> list[dict[str, object]]:
    """Send a message expecting SSE; collect events until 'done' or 'error'."""
    headers = {"accept": "text/event-stream"}
    events: list[dict[str, object]] = []
    async with client.stream(
        "POST",
        f"/api/v1/ws/{ws}/conversations/{conv}/messages",
        json=body,
        headers=headers,
    ) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[len("data: ") :])
            events.append(payload)
            if payload.get("type") in {"done", "error"}:
                return events
    return events


async def test_file_read_text_emits_file_source_citation(
    member_client_org_a: tuple[httpx.AsyncClient, str],
) -> None:
    """When the agent calls file_read on an attached markdown file and the
    result is kind='text', a citation event with source_type='file' is
    emitted on the SSE stream."""
    client, ws = member_client_org_a
    conv = await _make_conv(client, ws)
    content = b"# Geography Note\n\nThe capital of France is **Paris**.\n"
    fid = await _upload_text(client, ws, conv, content, "fact.md")

    events = await _drain_sse_to_done(
        client,
        ws,
        conv,
        {
            "content": (
                "Please read the attached file and tell me where Paris is. "
                "Cite the file in your answer."
            ),
            "attachments": [fid],
        },
    )
    assert any(e.get("type") == "done" for e in events), (
        f"stream did not reach 'done': {events[-5:] if events else []}"
    )

    citation_events = [e for e in events if e.get("type") == "citation"]
    assert citation_events, (
        "no citation events received — likely the LLM did not call file_read; "
        "consider tightening the prompt or pre-warming the sandbox."
    )

    file_citations = [
        e
        for e in citation_events
        if isinstance(e.get("data"), dict)
        and e["data"].get("metadata", {}).get("source_type") == "file"
    ]
    assert file_citations, (
        f"expected at least one source_type='file' citation, got: {citation_events}"
    )

    md = file_citations[0]["data"]["metadata"]
    assert isinstance(md.get("path"), str)
    assert md["path"].endswith("fact.md"), f"unexpected path: {md.get('path')}"
    chunks = file_citations[0]["data"].get("chunks", [])
    assert chunks, "citation has no chunks"
    assert any("Paris" in c.get("content", "") for c in chunks), (
        f"chunks don't reference Paris: {chunks}"
    )
