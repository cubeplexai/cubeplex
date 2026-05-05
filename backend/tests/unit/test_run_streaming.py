"""Run streaming runtime tests."""

import importlib
import json
from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from cubebox.api.routes.v1 import conversations as conversations_route
from cubebox.auth.context import RequestContext
from cubebox.cache import RedisHandle
from cubebox.models import Role
from cubebox.streams.run_events import create_run
from cubebox.streams.run_manager import RunManager


def FakeRedis() -> fakeredis.aioredis.FakeRedis:  # noqa: N802 — preserves call sites below
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


class _DummySessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeMessage:
    type = "ai"

    def __init__(self, content: str) -> None:
        self.content = content
        self.additional_kwargs: dict[str, object] = {}
        self.name = None
        self.tool_call_chunks: list[dict[str, object]] = []
        self.usage_metadata: dict[str, object] = {}


class _FakeAgent:
    async def astream(self, *_args, **_kwargs):
        yield ((), ("messages", (_FakeMessage("hello"), {})))
        yield ((), ("messages", (_FakeMessage(" world"), {})))


class _FakeStatefulAgent:
    async def aget_state(self, config: RunnableConfig):
        assert config["configurable"]["thread_id"] == "conv-1"
        return SimpleNamespace(
            values={
                "messages": [
                    SimpleNamespace(content="Earlier answer with citations 【2-1】 and 【5-1】"),
                    SimpleNamespace(
                        content=[
                            {"type": "text", "text": "Nested marker 【7-1】"},
                            {"type": "tool", "payload": {"note": "ignored"}},
                        ]
                    ),
                ]
            }
        )

    async def astream(self, *_args, **_kwargs):
        from cubebox.middleware.citations.counter import citation_counter_var

        counter = citation_counter_var.get()
        assert counter is not None
        yield ((), ("messages", (_FakeMessage(f"next citation {counter._next}"), {})))


class _FakeLLMFactory:
    async def create_default(self) -> object:
        return object()


class _FakeRegistry:
    def list_tools(self) -> list[object]:
        return []

    def get_content_type(self, _tool_name: str) -> None:
        return None


async def _fake_get_by_id(self, conversation_id: str):
    return SimpleNamespace(id=conversation_id, title=conversation_id)


async def _noop_update_timestamp(*args, **kwargs) -> None:
    return None


@pytest.mark.asyncio
async def test_run_bootstrap_and_stream_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    app = SimpleNamespace(state=SimpleNamespace())
    app.state.redis = redis
    app.state.redis_key_prefix = "test"
    app.state.checkpointer_factory = lambda: MemorySaver()
    app.state.sandbox_factory = lambda: None
    app.state.run_manager = RunManager(
        app=app,
        redis=redis,
        key_prefix="test",
        run_event_ttl_seconds=900,
    )

    raw_request = SimpleNamespace(app=app, headers={})
    fake_user = SimpleNamespace(id="test-user", email="test@example.com")
    ctx = RequestContext(  # type: ignore[arg-type]
        user=fake_user,
        org_id="org-00000000000000",
        workspace_id="ws-00000000000000",
        role=Role.ADMIN,
    )

    monkeypatch.setattr(
        conversations_route,
        "async_session_maker",
        lambda: _DummySessionContext(),
    )
    monkeypatch.setattr(conversations_route.ConversationRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(
        conversations_route,
        "_update_conversation_timestamp",
        _noop_update_timestamp,
    )

    import cubebox.agents.graph
    import cubebox.llm.factory
    import cubebox.tools

    monkeypatch.setattr(
        cubebox.agents.graph, "create_cubebox_agent", lambda **_kwargs: _FakeAgent()
    )
    monkeypatch.setattr(cubebox.llm.factory, "LLMFactory", _FakeLLMFactory)
    monkeypatch.setattr(cubebox.tools, "get_registry", lambda: _FakeRegistry())

    rds = RedisHandle(client=redis, key_prefix="test")
    send_response = await conversations_route.send_message(
        "conv-1",
        conversations_route.SendMessageRequest(content="hi"),
        raw_request,
        ctx,
        rds,
    )
    assert isinstance(send_response, conversations_route.SendMessageResponse)
    run_id = send_response.run_id

    bootstrap = await conversations_route.get_conversation_bootstrap(
        "conv-1",
        raw_request,
        object(),
        ctx,
        rds,
    )
    assert bootstrap["active_run"]["run_id"] == run_id
    assert bootstrap["active_run"]["user_message"] == "hi"

    stream_response = await conversations_route.stream_run(
        "conv-1", run_id, raw_request, object(), ctx, rds
    )
    events: list[dict[str, object]] = []
    async for chunk in stream_response.body_iterator:
        line = chunk.strip()
        if isinstance(line, bytes):
            line = line.decode()
        for part in str(line).split("\n"):
            if part.startswith("data: "):
                events.append(json.loads(part[6:]))
        if events and events[-1]["type"] == "done":
            break

    assert [event["type"] for event in events] == ["text_delta", "text_delta", "done"]
    assert all("event_id" in event for event in events)


@pytest.mark.asyncio
async def test_create_run_claim_is_atomic() -> None:
    redis = FakeRedis()

    first = await create_run(
        redis,
        prefix="test",
        run_id="run-1",
        conversation_id="conv-1",
        status="running",
        started_at="2026-04-23T00:00:00Z",
        ttl_seconds=60,
    )
    second = await create_run(
        redis,
        prefix="test",
        run_id="run-2",
        conversation_id="conv-1",
        status="running",
        started_at="2026-04-23T00:00:01Z",
        ttl_seconds=60,
    )

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_run_recovers_citation_counter_from_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    app = SimpleNamespace(state=SimpleNamespace())
    app.state.redis = redis
    app.state.redis_key_prefix = "test"
    app.state.checkpointer_factory = lambda: MemorySaver()
    app.state.sandbox_factory = lambda: None
    app.state.run_manager = RunManager(
        app=app,
        redis=redis,
        key_prefix="test",
        run_event_ttl_seconds=900,
    )

    raw_request = SimpleNamespace(app=app, headers={})
    fake_user = SimpleNamespace(id="test-user", email="test@example.com")
    ctx = RequestContext(  # type: ignore[arg-type]
        user=fake_user,
        org_id="org-00000000000000",
        workspace_id="ws-00000000000000",
        role=Role.ADMIN,
    )

    monkeypatch.setattr(
        conversations_route,
        "async_session_maker",
        lambda: _DummySessionContext(),
    )
    monkeypatch.setattr(conversations_route.ConversationRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(
        conversations_route,
        "_update_conversation_timestamp",
        _noop_update_timestamp,
    )

    import cubebox.agents.graph
    import cubebox.llm.factory
    import cubebox.tools

    monkeypatch.setattr(
        cubebox.agents.graph,
        "create_cubebox_agent",
        lambda **_kwargs: _FakeStatefulAgent(),
    )
    monkeypatch.setattr(cubebox.llm.factory, "LLMFactory", _FakeLLMFactory)
    monkeypatch.setattr(cubebox.tools, "get_registry", lambda: _FakeRegistry())

    rds = RedisHandle(client=redis, key_prefix="test")
    send_response = await conversations_route.send_message(
        "conv-1",
        conversations_route.SendMessageRequest(content="hi"),
        raw_request,
        ctx,
        rds,
    )
    stream_response = await conversations_route.stream_run(
        "conv-1", send_response.run_id, raw_request, object(), ctx, rds
    )

    events: list[dict[str, object]] = []
    async for chunk in stream_response.body_iterator:
        line = chunk.strip()
        if isinstance(line, bytes):
            line = line.decode()
        for part in str(line).split("\n"):
            if part.startswith("data: "):
                events.append(json.loads(part[6:]))
        if events and events[-1]["type"] == "done":
            break

    assert events[0]["type"] == "text_delta"
    assert events[0]["data"]["content"] == "next citation 8"


@pytest.mark.asyncio
async def test_run_appends_db_mcp_tools_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    app = SimpleNamespace(state=SimpleNamespace())
    app.state.redis = redis
    app.state.redis_key_prefix = "test"
    app.state.checkpointer_factory = lambda: MemorySaver()
    app.state.sandbox_factory = lambda: None
    app.state.encryption_backend = object()
    app.state.mcp_user_token_signer = object()
    app.state.run_manager = RunManager(
        app=app,
        redis=redis,
        key_prefix="test",
        run_event_ttl_seconds=900,
    )

    raw_request = SimpleNamespace(app=app, headers={})
    fake_user = SimpleNamespace(id="test-user", email="test@example.com")
    ctx = RequestContext(  # type: ignore[arg-type]
        user=fake_user,
        org_id="org-00000000000000",
        workspace_id="ws-00000000000000",
        role=Role.ADMIN,
    )

    monkeypatch.setattr(
        conversations_route,
        "async_session_maker",
        lambda: _DummySessionContext(),
    )
    monkeypatch.setattr(conversations_route.ConversationRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(
        conversations_route,
        "_update_conversation_timestamp",
        _noop_update_timestamp,
    )

    import cubebox.agents.graph
    import cubebox.llm.factory
    import cubebox.mcp.runtime
    import cubebox.tools

    db_engine_module = importlib.import_module("cubebox.db.engine")

    base_tool = object()
    db_tool = object()
    captured_tools: list[object] = []

    async def _load_db_tools(**kwargs):
        assert kwargs["org_id"] == "org-00000000000000"
        assert kwargs["workspace_id"] == "ws-00000000000000"
        assert kwargs["user_id"] == "test-user"
        return [db_tool]

    def _create_agent(**kwargs):
        captured_tools.extend(kwargs["tools"])
        return _FakeAgent()

    monkeypatch.setattr(db_engine_module, "async_session_maker", lambda: _DummySessionContext())
    monkeypatch.setattr(cubebox.mcp.runtime, "load_mcp_tools_for_workspace", _load_db_tools)
    monkeypatch.setattr(cubebox.agents.graph, "create_cubebox_agent", _create_agent)
    monkeypatch.setattr(cubebox.llm.factory, "LLMFactory", _FakeLLMFactory)
    monkeypatch.setattr(
        cubebox.tools, "get_registry", lambda: SimpleNamespace(list_tools=lambda: [base_tool])
    )

    rds = RedisHandle(client=redis, key_prefix="test")
    send_response = await conversations_route.send_message(
        "conv-1",
        conversations_route.SendMessageRequest(content="hi"),
        raw_request,
        ctx,
        rds,
    )
    stream_response = await conversations_route.stream_run(
        "conv-1", send_response.run_id, raw_request, object(), ctx, rds
    )

    async for chunk in stream_response.body_iterator:
        line = chunk.strip()
        if isinstance(line, bytes):
            line = line.decode()
        if '"type": "done"' in str(line):
            break

    assert captured_tools == [base_tool, db_tool]
