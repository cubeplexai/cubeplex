"""E2E tests for graceful restart drain + stale-run detection."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from redis.asyncio import Redis

from cubebox.api.middleware.drain import DrainMiddleware
from cubebox.config import config as _cubebox_config
from cubebox.lifecycle.drain import DrainState
from cubebox.streams.run_events import (
    append_run_event,
    create_run,
    get_run_meta,
)
from cubebox.streams.run_manager import RunManager

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def redis_client() -> Redis:
    client = Redis.from_url(
        _cubebox_config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=True,
    )
    yield client
    await client.aclose()


async def test_append_run_event_stamps_last_event_at(redis_client: Redis) -> None:
    prefix = "test_graceful"
    run_id = "run-heartbeat-1"
    conv_id = "conv-heartbeat-1"
    started = datetime.now(UTC).isoformat()

    meta = await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=started,
        ttl_seconds=60,
    )
    assert meta is not None

    before = datetime.now(UTC)
    await append_run_event(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        payload={"type": "status", "data": {"phase": "test"}},
        ttl_seconds=60,
        maxlen=100,
    )
    after = datetime.now(UTC)

    fresh_meta = await get_run_meta(redis_client, prefix=prefix, run_id=run_id)
    assert fresh_meta is not None
    assert fresh_meta.last_event_at is not None
    parsed = datetime.fromisoformat(fresh_meta.last_event_at)
    assert before - timedelta(seconds=1) <= parsed <= after + timedelta(seconds=1)


def _make_run_manager(redis_client: Redis) -> RunManager:
    app = SimpleNamespace(state=SimpleNamespace())
    return RunManager(
        app=app,  # type: ignore[arg-type]
        redis=redis_client,
        key_prefix="test_drain",
        run_event_ttl_seconds=60,
    )


@pytest.mark.asyncio
async def test_drain_returns_immediately_when_no_tasks(redis_client: Redis) -> None:
    rm = _make_run_manager(redis_client)
    start = time.monotonic()
    await rm.drain(timeout_seconds=10.0)
    elapsed = time.monotonic() - start
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_drain_waits_for_in_flight_task(redis_client: Redis) -> None:
    rm = _make_run_manager(redis_client)

    async def slow() -> None:
        await asyncio.sleep(0.3)

    task = asyncio.create_task(slow(), name="run:slow-1")
    rm._tasks["slow-1"] = task
    rm._tasks_empty.clear()
    task.add_done_callback(lambda _: rm._on_task_done("slow-1"))

    start = time.monotonic()
    await rm.drain(timeout_seconds=5.0)
    elapsed = time.monotonic() - start
    assert 0.2 < elapsed < 1.5
    assert "slow-1" not in rm._tasks


@pytest.mark.asyncio
async def test_drain_timeout_cancels_residual(redis_client: Redis) -> None:
    rm = _make_run_manager(redis_client)

    async def forever() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(forever(), name="run:forever")
    rm._tasks["forever"] = task
    rm._tasks_empty.clear()
    task.add_done_callback(lambda _: rm._on_task_done("forever"))

    await rm.drain(timeout_seconds=0.2)
    # cancel_all path completed: task is done (cancelled) and removed.
    assert task.cancelled()


@pytest.mark.asyncio
async def test_drain_middleware_passthrough_when_accepting() -> None:
    state = DrainState()
    received_scope: dict[str, object] = {}

    async def downstream(scope, receive, send):
        received_scope["called"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = DrainMiddleware(downstream, drain_state=state)
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request"}

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/ws/ws-1/conversations/c-1/messages",
    }
    await mw(scope, receive, send)
    assert received_scope.get("called") is True
    assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_drain_middleware_blocks_new_run_when_draining() -> None:
    state = DrainState()
    state.enter_draining()

    async def downstream(scope, receive, send):
        raise AssertionError("downstream must not be called during drain")

    mw = DrainMiddleware(downstream, drain_state=state)
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request"}

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/ws/ws-1/conversations/c-1/messages",
    }
    await mw(scope, receive, send)
    assert sent[0]["status"] == 503
    headers = dict(sent[0]["headers"])
    assert headers[b"retry-after"] == b"5"


@pytest.mark.asyncio
async def test_drain_middleware_passes_through_non_run_paths_when_draining() -> None:
    state = DrainState()
    state.enter_draining()

    called = {"yes": False}

    async def downstream(scope, receive, send):
        called["yes"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = DrainMiddleware(downstream, drain_state=state)

    async def receive():
        return {"type": "http.request"}

    async def send(_msg):
        pass

    # SSE subscription should pass through
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/ws/ws-1/conversations/c-1/runs/r-1/stream",
    }
    await mw(scope, receive, send)
    assert called["yes"] is True


@pytest.mark.asyncio
async def test_health_live_always_200(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_legacy_health_removed(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.get("/health")
    assert resp.status_code == 404


@pytest_asyncio.fixture
async def app_state_drain(memory_client: httpx.AsyncClient) -> DrainState:
    """Pull the live DrainState from the app the test client targets."""
    app = memory_client._transport.app  # type: ignore[attr-defined]
    state = getattr(app.state, "drain_state", None)
    assert isinstance(state, DrainState), "lifespan did not install drain_state"
    return state


@pytest.mark.asyncio
async def test_post_messages_returns_503_when_draining(
    memory_client: httpx.AsyncClient,
    app_state_drain: DrainState,
) -> None:
    # Create a conversation first while still accepting.
    create_resp = await memory_client.post(
        "/api/v1/ws/default-ws/conversations", params={"title": "drain-test"}
    )
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["id"]

    app_state_drain.enter_draining()
    try:
        resp = await memory_client.post(
            f"/api/v1/ws/default-ws/conversations/{conv_id}/messages",
            json={"content": "hi"},
        )
        assert resp.status_code == 503
        assert resp.headers.get("retry-after") == "5"
    finally:
        app_state_drain._state = "accepting"  # type: ignore[attr-defined]
