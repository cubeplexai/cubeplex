"""E2E test: SSE stream format validation."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from cubebox.api.routes.v1 import conversations as conversations_route


@pytest.mark.asyncio
async def test_sse_response_content_type(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post("/api/v1/conversations", params={"title": "test"})
    conv_id = resp.json()["id"]

    async with memory_client.stream(
        "POST",
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Say hi."},
    ) as response:
        assert "text/event-stream" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_every_event_is_valid_json(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post("/api/v1/conversations", params={"title": "test"})
    conv_id = resp.json()["id"]

    async with memory_client.stream(
        "POST",
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Say hi."},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                assert "type" in payload
                assert "timestamp" in payload


@pytest.mark.asyncio
async def test_stream_always_ends_with_done(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post("/api/v1/conversations", params={"title": "test"})
    conv_id = resp.json()["id"]

    events = []
    async with memory_client.stream(
        "POST",
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Say hi."},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_agent_id_is_null_for_main_agent(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post("/api/v1/conversations", params={"title": "test"})
    conv_id = resp.json()["id"]

    events = []
    async with memory_client.stream(
        "POST",
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Say hi."},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    text_events = [e for e in events if e["type"] == "text_delta"]
    assert len(text_events) > 0
    for e in text_events:
        assert e.get("agent_id") is None


@pytest.mark.asyncio
async def test_client_disconnect_cancels_stream_before_closing_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slow_agent_cancelled = asyncio.Event()
    checkpointer_closed_before_cancel = asyncio.Event()

    class _DummySessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class _FakeConn:
        def close(self) -> None:
            if not slow_agent_cancelled.is_set():
                checkpointer_closed_before_cancel.set()

    class _FakeCheckpointer:
        def __init__(self) -> None:
            self.conn = _FakeConn()

    class _FakeMessage:
        def __init__(self, content: str) -> None:
            self.content = content
            self.additional_kwargs: dict[str, object] = {}
            self.name = None

    class _SlowAgent:
        async def astream(self, *_args, **_kwargs):
            yield ("messages", (_FakeMessage("slow"), {}))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                slow_agent_cancelled.set()
                raise

    class _FastAgent:
        async def astream(self, *_args, **_kwargs):
            yield ("messages", (_FakeMessage("fast"), {}))

    def _fake_session_maker() -> _DummySessionContext:
        return _DummySessionContext()

    async def _fake_get_by_id(self, conversation_id: str):
        return SimpleNamespace(id=conversation_id, title=conversation_id)

    async def _noop_update_timestamp(_conversation_id: str) -> None:
        return None

    def _fake_create_cubebox_agent(*, conversation_id: str, **_kwargs):
        if conversation_id == "slow":
            return _SlowAgent()
        return _FastAgent()

    monkeypatch.setattr(conversations_route, "async_session_maker", _fake_session_maker)
    monkeypatch.setattr(
        conversations_route, "_update_conversation_timestamp", _noop_update_timestamp
    )
    monkeypatch.setattr(
        conversations_route.ConversationRepository,
        "get_by_id",
        _fake_get_by_id,
    )
    monkeypatch.setattr(
        "cubebox.agents.graph.create_cubebox_agent",
        _fake_create_cubebox_agent,
    )

    raw_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer_factory=_FakeCheckpointer,
                sandbox_factory=lambda: None,
                skills=[],
            )
        ),
        state=SimpleNamespace(user_id="test-user"),
    )

    slow_response = await conversations_route.send_message(
        "slow",
        conversations_route.SendMessageRequest(content="slow request"),
        raw_request,
    )
    slow_iter = slow_response.body_iterator.__aiter__()

    first_chunk = await anext(slow_iter)
    first_event = json.loads(first_chunk.removeprefix("data: ").strip())
    assert first_event["type"] == "text_delta"

    pending_chunk = asyncio.create_task(anext(slow_iter))
    await asyncio.sleep(0.05)
    pending_chunk.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending_chunk

    await asyncio.wait_for(slow_agent_cancelled.wait(), timeout=1)
    assert not checkpointer_closed_before_cancel.is_set()

    fast_response = await conversations_route.send_message(
        "fast",
        conversations_route.SendMessageRequest(content="fast request"),
        raw_request,
    )
    events = []
    async for chunk in fast_response.body_iterator:
        payload = chunk.removeprefix("data: ").strip()
        if payload:
            events.append(json.loads(payload))

    assert events[-1]["type"] == "done"
