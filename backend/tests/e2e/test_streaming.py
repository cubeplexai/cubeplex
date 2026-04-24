"""E2E test: SSE stream format validation."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from redis.asyncio import Redis

from cubebox.api.routes.v1 import conversations as conversations_route
from cubebox.auth.context import RequestContext
from cubebox.config import config as _cubebox_config
from cubebox.models import Role
from cubebox.streams.run_manager import RunManager

pytestmark = pytest.mark.e2e


def _make_fake_ctx() -> RequestContext:
    """Build a RequestContext for unit-style direct route invocation."""
    fake_user = SimpleNamespace(id="test-user", email="test@example.com")
    return RequestContext(
        user=fake_user,  # type: ignore[arg-type]
        org_id="default-org",
        workspace_id="default-ws",
        role=Role.ADMIN,
    )


def _make_streaming_request_state(
    *,
    checkpointer_factory: object,
    sandbox_factory: object,
    skills: list[object] | None = None,
) -> SimpleNamespace:
    # Real Redis — matches the `e2e` marker contract (hits real services).
    # The autouse _flush_test_redis fixture in conftest clears state between tests.
    redis = Redis.from_url(
        _cubebox_config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=True,
    )
    app = SimpleNamespace(state=SimpleNamespace())
    app.state.checkpointer_factory = checkpointer_factory
    app.state.sandbox_factory = sandbox_factory
    app.state.skills = skills or []
    app.state.redis = redis
    app.state.redis_key_prefix = "test"
    app.state.run_manager = RunManager(
        app=app,
        redis=redis,
        key_prefix="test",
        run_event_ttl_seconds=900,
    )
    return app


@pytest.mark.asyncio
async def test_sse_response_content_type(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post("/api/v1/ws/default-ws/conversations", params={"title": "test"})
    conv_id = resp.json()["id"]

    async with memory_client.stream(
        "POST",
        f"/api/v1/ws/default-ws/conversations/{conv_id}/messages",
        json={"content": "Say hi."},
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    ) as response:
        assert "text/event-stream" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_every_event_is_valid_json(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post("/api/v1/ws/default-ws/conversations", params={"title": "test"})
    conv_id = resp.json()["id"]

    async with memory_client.stream(
        "POST",
        f"/api/v1/ws/default-ws/conversations/{conv_id}/messages",
        json={"content": "Say hi."},
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                assert "type" in payload
                assert "timestamp" in payload


@pytest.mark.asyncio
async def test_stream_always_ends_with_done(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post("/api/v1/ws/default-ws/conversations", params={"title": "test"})
    conv_id = resp.json()["id"]

    events = []
    async with memory_client.stream(
        "POST",
        f"/api/v1/ws/default-ws/conversations/{conv_id}/messages",
        json={"content": "Say hi."},
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert events[-1]["type"] == "done"


def test_tool_call_delta_identity_is_backfilled_per_agent() -> None:
    context: dict[tuple[str | None, int], dict[str, object]] = {}
    events = conversations_route._dicts_to_sse_events(
        [
            {
                "type": "tool_call_delta",
                "timestamp": "",
                "data": {
                    "tool_call_id": "tc-a",
                    "name": "write_file",
                    "args_delta": '{"file_path":"a.py"',
                    "index": 0,
                },
                "agent_id": "subagent:a",
            },
            {
                "type": "tool_call_delta",
                "timestamp": "",
                "data": {
                    "tool_call_id": "tc-b",
                    "name": "write_file",
                    "args_delta": '{"file_path":"b.py"',
                    "index": 0,
                },
                "agent_id": "subagent:b",
            },
            {
                "type": "tool_call_delta",
                "timestamp": "",
                "data": {
                    "tool_call_id": None,
                    "name": None,
                    "args_delta": ',"content":"print(1)"}',
                    "index": 0,
                },
                "agent_id": "subagent:a",
            },
            {
                "type": "tool_call_delta",
                "timestamp": "",
                "data": {
                    "tool_call_id": None,
                    "name": None,
                    "args_delta": ',"content":"print(2)"}',
                    "index": 0,
                },
                "agent_id": "subagent:b",
            },
        ],
        context,
    )

    assert events[2].data["tool_call_id"] == "tc-a"
    assert events[2].data["name"] == "write_file"
    assert events[3].data["tool_call_id"] == "tc-b"
    assert events[3].data["name"] == "write_file"


@pytest.mark.asyncio
async def test_agent_id_is_null_for_main_agent(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post("/api/v1/ws/default-ws/conversations", params={"title": "test"})
    conv_id = resp.json()["id"]

    events = []
    async with memory_client.stream(
        "POST",
        f"/api/v1/ws/default-ws/conversations/{conv_id}/messages",
        json={"content": "Say hi."},
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
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
        # type="ai" so convert_messages_chunk emits text_delta
        # (post 131dabf, non-AI messages are filtered out).
        type = "ai"

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

    async def _noop_update_timestamp(
        _conversation_id: str, *, org_id: str, workspace_id: str, user_id: str
    ) -> None:
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

    app = _make_streaming_request_state(
        checkpointer_factory=_FakeCheckpointer,
        sandbox_factory=lambda: None,
    )
    raw_request = SimpleNamespace(
        app=app,
        headers={"accept": "text/event-stream"},
        state=SimpleNamespace(user_id="test-user"),
    )
    from cubebox.cache import RedisHandle

    rds = RedisHandle(client=app.state.redis, key_prefix=app.state.redis_key_prefix)

    slow_response = await conversations_route.send_message(
        "slow",
        conversations_route.SendMessageRequest(content="slow request"),
        raw_request,
        _make_fake_ctx(),
        rds,
    )
    slow_iter = slow_response.body_iterator.__aiter__()

    first_chunk = await anext(slow_iter)
    first_event = json.loads(str(first_chunk).split("data: ", 1)[1].strip())
    assert first_event["type"] == "text_delta"

    pending_chunk = asyncio.create_task(anext(slow_iter))
    await asyncio.sleep(0.05)
    pending_chunk.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending_chunk

    await asyncio.sleep(0.05)
    assert not slow_agent_cancelled.is_set()
    assert not checkpointer_closed_before_cancel.is_set()

    await app.state.run_manager.shutdown()
    await asyncio.wait_for(slow_agent_cancelled.wait(), timeout=1)
    assert not checkpointer_closed_before_cancel.is_set()

    fast_response = await conversations_route.send_message(
        "fast",
        conversations_route.SendMessageRequest(content="fast request"),
        raw_request,
        _make_fake_ctx(),
        rds,
    )
    events = []
    async for chunk in fast_response.body_iterator:
        for line in str(chunk).splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_tool_call_delta_events_in_sse_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tool_call_delta events should appear in SSE stream for tool_call_chunks."""
    from langchain_core.messages import AIMessageChunk

    class _DummySessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class _FakeConn:
        def close(self) -> None:
            pass

    class _FakeCheckpointer:
        def __init__(self) -> None:
            self.conn = _FakeConn()

    class _FakeToolCallAgent:
        async def astream(self, *_args, **_kwargs):
            # First chunk: tool name + start of args
            chunk1 = AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "write_file",
                        "args": '{"file_path": "/app/main.py", "content": "import ',
                        "id": "tc_1",
                        "index": 0,
                    }
                ],
            )
            yield ("messages", (chunk1, {}))

            # Second chunk: continuation
            chunk2 = AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"name": None, "args": 'os\\nimport sys"}', "id": None, "index": 0}
                ],
            )
            yield ("messages", (chunk2, {}))

    def _fake_session_maker() -> _DummySessionContext:
        return _DummySessionContext()

    async def _fake_get_by_id(self, conversation_id: str):
        return SimpleNamespace(id=conversation_id, title=conversation_id)

    async def _noop_update_timestamp(
        _conversation_id: str, *, org_id: str, workspace_id: str, user_id: str
    ) -> None:
        return None

    monkeypatch.setattr(conversations_route, "async_session_maker", _fake_session_maker)
    monkeypatch.setattr(
        conversations_route, "_update_conversation_timestamp", _noop_update_timestamp
    )
    monkeypatch.setattr(conversations_route.ConversationRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(
        "cubebox.agents.graph.create_cubebox_agent",
        lambda **_kwargs: _FakeToolCallAgent(),
    )

    app = _make_streaming_request_state(
        checkpointer_factory=_FakeCheckpointer,
        sandbox_factory=lambda: None,
    )
    raw_request = SimpleNamespace(
        app=app,
        headers={"accept": "text/event-stream"},
        state=SimpleNamespace(user_id="test-user"),
    )
    from cubebox.cache import RedisHandle

    rds = RedisHandle(client=app.state.redis, key_prefix=app.state.redis_key_prefix)

    response = await conversations_route.send_message(
        "test-conv",
        conversations_route.SendMessageRequest(content="write a file"),
        raw_request,
        _make_fake_ctx(),
        rds,
    )

    events = []
    async for chunk in response.body_iterator:
        for line in str(chunk).splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    delta_events = [e for e in events if e["type"] == "tool_call_delta"]
    assert len(delta_events) == 2, f"Expected 2 tool_call_delta events, got {len(delta_events)}"
    assert delta_events[0]["data"]["name"] == "write_file"
    assert delta_events[0]["data"]["tool_call_id"] == "tc_1"
    assert delta_events[1]["data"]["name"] == "write_file"
    assert delta_events[1]["data"]["tool_call_id"] == "tc_1"
    assert delta_events[1]["data"]["args_delta"] == 'os\\nimport sys"}'
