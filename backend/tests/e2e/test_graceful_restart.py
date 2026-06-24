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
    is_stale_meta,
    mark_run_stale,
)
from cubebox.streams.run_manager import RunManager
from tests.e2e.conftest import DEFAULT_WS_ID

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


@pytest.mark.asyncio
async def test_health_ready_200_when_accepting(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


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
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "drain-test"}
    )
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["id"]

    app_state_drain.enter_draining()
    try:
        resp = await memory_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/messages",
            json={"content": "hi"},
        )
        assert resp.status_code == 503
        assert resp.headers.get("retry-after") == "5"
    finally:
        app_state_drain._state = "accepting"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_health_ready_503_when_draining(
    memory_client: httpx.AsyncClient,
    app_state_drain: DrainState,
) -> None:
    app_state_drain.enter_draining()
    try:
        ready_resp = await memory_client.get("/health/ready")
        assert ready_resp.status_code == 503
        assert ready_resp.json() == {"status": "draining"}
        # Liveness must remain 200 — k8s should not kill the pod during drain.
        live_resp = await memory_client.get("/health/live")
        assert live_resp.status_code == 200
    finally:
        app_state_drain._state = "accepting"  # type: ignore[attr-defined]


def test_is_stale_meta_detects_stale_running() -> None:
    from datetime import UTC, datetime, timedelta

    from cubebox.streams.run_events import RunMeta

    now = datetime.now(UTC)
    fresh = RunMeta(
        run_id="r1",
        conversation_id="c1",
        status="running",
        started_at=now.isoformat(),
        last_event_at=(now - timedelta(seconds=5)).isoformat(),
    )
    stale = RunMeta(
        run_id="r2",
        conversation_id="c2",
        status="running",
        started_at=now.isoformat(),
        last_event_at=(now - timedelta(seconds=300)).isoformat(),
    )
    completed = RunMeta(
        run_id="r3",
        conversation_id="c3",
        status="completed",
        started_at=now.isoformat(),
        last_event_at=(now - timedelta(seconds=300)).isoformat(),
    )

    # Worker died before producing the first event: last_event_at is None
    # and is_stale_meta must fall back to started_at.
    no_heartbeat = RunMeta(
        run_id="r4",
        conversation_id="c4",
        status="running",
        started_at=(now - timedelta(seconds=300)).isoformat(),
    )

    assert not is_stale_meta(fresh, threshold_seconds=120, now=now)
    assert is_stale_meta(stale, threshold_seconds=120, now=now)
    # Completed runs are never stale, regardless of age.
    assert not is_stale_meta(completed, threshold_seconds=120, now=now)
    assert is_stale_meta(no_heartbeat, threshold_seconds=120, now=now)


@pytest.mark.asyncio
async def test_mark_run_stale_clears_active_and_sets_status(redis_client: Redis) -> None:
    from datetime import UTC, datetime

    from cubebox.streams.run_events import (
        create_run,
        get_active_run,
        get_run_meta,
    )

    prefix = "test_stale_mark"
    run_id = "r-stale-1"
    conv_id = "c-stale-1"
    await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )

    await mark_run_stale(redis_client, prefix=prefix, run_id=run_id, conversation_id=conv_id)

    fresh = await get_run_meta(redis_client, prefix=prefix, run_id=run_id)
    assert fresh is not None
    assert fresh.status == "stale"
    active = await get_active_run(redis_client, prefix=prefix, conversation_id=conv_id)
    assert active is None


@pytest.mark.asyncio
async def test_mark_run_stale_is_idempotent(redis_client: Redis) -> None:
    from datetime import UTC, datetime

    from cubebox.streams.run_events import create_run, get_run_meta

    prefix = "test_stale_idem"
    run_id = "r-stale-2"
    conv_id = "c-stale-2"
    await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )

    await mark_run_stale(redis_client, prefix=prefix, run_id=run_id, conversation_id=conv_id)
    # Second call: status already stale, active_run already cleared — no-op.
    await mark_run_stale(redis_client, prefix=prefix, run_id=run_id, conversation_id=conv_id)
    fresh = await get_run_meta(redis_client, prefix=prefix, run_id=run_id)
    assert fresh is not None
    assert fresh.status == "stale"


@pytest.mark.asyncio
async def test_update_run_meta_status_cas_preserves_stale(redis_client: Redis) -> None:
    """If a worker tries to flip status from stale → completed, the CAS must reject it."""
    from datetime import UTC, datetime

    from cubebox.streams.run_events import create_run, update_run_meta

    prefix = "test_cas_stale"
    run_id = "r-cas-1"
    conv_id = "c-cas-1"
    await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )

    # Detection-then-recovery flips to stale.
    await mark_run_stale(redis_client, prefix=prefix, run_id=run_id, conversation_id=conv_id)

    # Worker's "I finished" path tries to overwrite. CAS must drop the write.
    after = await update_run_meta(redis_client, prefix=prefix, run_id=run_id, status="completed")
    assert after is not None
    assert after.status == "stale", "CAS leaked through; stale flag was overwritten"


@pytest.mark.asyncio
async def test_create_run_succeeds_after_stale_recovery(redis_client: Redis) -> None:
    """Stranded-run scenario: a crashed process left status=running in redis.

    The next caller (e.g. IM worker) must be able to mark the stranded run
    stale and start a fresh run for the same conversation. Without the
    staleness sweep inside RunManager.start_run this loops forever on
    "already has an active run".
    """
    import uuid
    from datetime import UTC, datetime, timedelta

    from cubebox.streams.run_events import (
        create_run,
        get_active_run,
        get_run_meta,
    )

    # Unique prefix per invocation so we don't trip on residual state from
    # an earlier failed run (redis_client doesn't FLUSHDB between tests).
    prefix = f"test_stale_recovery_{uuid.uuid4().hex[:8]}"
    conv_id = "c-stale-recover"
    stranded_run_id = "r-stranded"
    fresh_run_id = "r-fresh"
    long_ago = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    now_iso = datetime.now(UTC).isoformat()

    # 1. Simulate a process that started a run and crashed before any event:
    #    status=running, last_event_at=None, started_at=10min ago.
    await create_run(
        redis_client,
        prefix=prefix,
        run_id=stranded_run_id,
        conversation_id=conv_id,
        status="running",
        started_at=long_ago,
        ttl_seconds=3600,
    )

    # 2. The next caller's create_run is rejected because the active-run key
    #    still points at the stranded run.
    blocked = await create_run(
        redis_client,
        prefix=prefix,
        run_id=fresh_run_id,
        conversation_id=conv_id,
        status="running",
        started_at=now_iso,
        ttl_seconds=3600,
    )
    assert blocked is None, "stranded active run should block a fresh create_run"

    # 3. The caller checks staleness — well past threshold — and finalizes it.
    existing = await get_active_run(redis_client, prefix=prefix, conversation_id=conv_id)
    assert existing is not None and existing.status == "running"
    assert is_stale_meta(existing, threshold_seconds=120)
    await mark_run_stale(
        redis_client, prefix=prefix, run_id=stranded_run_id, conversation_id=conv_id
    )

    # 4. After mark_run_stale, the active-run key is cleared and a retry of
    #    create_run for the same conversation succeeds.
    retry = await create_run(
        redis_client,
        prefix=prefix,
        run_id=fresh_run_id,
        conversation_id=conv_id,
        status="running",
        started_at=now_iso,
        ttl_seconds=3600,
    )
    assert retry is not None, "create_run should succeed once the stranded run is finalized"
    assert retry.run_id == fresh_run_id

    # 5. The stranded run row is preserved as stale (for audit/UI), while the
    #    active-run pointer now references the new run.
    stranded_meta = await get_run_meta(redis_client, prefix=prefix, run_id=stranded_run_id)
    assert stranded_meta is not None and stranded_meta.status == "stale"
    active = await get_active_run(redis_client, prefix=prefix, conversation_id=conv_id)
    assert active is not None and active.run_id == fresh_run_id


@pytest.mark.asyncio
async def test_update_run_meta_status_cas_allows_running_transition(
    redis_client: Redis,
) -> None:
    """Normal running → completed transition must still happen."""
    from datetime import UTC, datetime

    from cubebox.streams.run_events import create_run, update_run_meta

    prefix = "test_cas_running"
    run_id = "r-cas-2"
    conv_id = "c-cas-2"
    await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )

    after = await update_run_meta(redis_client, prefix=prefix, run_id=run_id, status="completed")
    assert after is not None
    assert after.status == "completed"


@pytest.mark.asyncio
async def test_bootstrap_clears_stale_run_and_sets_last_run_status(
    memory_client: httpx.AsyncClient, redis_client: Redis
) -> None:
    from datetime import UTC, datetime, timedelta

    from cubebox.streams.run_events import _active_run_key, create_run

    create_resp = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "stale-test"}
    )
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["id"]

    # The app under test uses prefix "test:test" (env=test). Reach it from app.state.
    app = memory_client._transport.app  # type: ignore[attr-defined]
    prefix = app.state.redis_key_prefix
    run_id = "stale-run-1"
    long_ago = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()

    # Plant a fake active run with an old last_event_at.
    meta = await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=long_ago,
        ttl_seconds=120,
    )
    assert meta is not None
    # Stamp last_event_at directly (no real append, so we set the hash field).
    await redis_client.hset(  # type: ignore[misc]
        f"{prefix}:run_meta:v2:{run_id}", "last_event_at", long_ago
    )

    resp = await memory_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/bootstrap")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_run"] is None
    assert body["last_run_status"] == "stale"

    # Active key cleared.
    active = await redis_client.get(_active_run_key(prefix, conv_id))
    assert active is None


@pytest.mark.asyncio
async def test_stream_subscribe_emits_stale_error_for_dead_run(
    memory_client: httpx.AsyncClient, redis_client: Redis
) -> None:
    from datetime import UTC, datetime, timedelta

    from cubebox.streams.run_events import create_run

    create_resp = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "stale-stream"}
    )
    conv_id = create_resp.json()["id"]

    app = memory_client._transport.app  # type: ignore[attr-defined]
    prefix = app.state.redis_key_prefix
    run_id = "stale-run-2"
    long_ago = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=long_ago,
        ttl_seconds=120,
    )
    await redis_client.hset(  # type: ignore[misc]
        f"{prefix}:run_meta:v2:{run_id}", "last_event_at", long_ago
    )

    async with memory_client.stream(
        "GET",
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/runs/{run_id}/stream",
    ) as resp:
        body = await resp.aread()
    text = body.decode()
    # SSE chunks are concatenated; find any data: line that contains run_stale.
    assert "run_stale" in text


@pytest.mark.asyncio
async def test_drain_waits_for_in_flight_run_then_returns(
    memory_client: httpx.AsyncClient,
) -> None:
    """Plant a slow async task in run_manager._tasks; drain should wait for it."""
    app = memory_client._transport.app  # type: ignore[attr-defined]
    rm = app.state.run_manager

    completed = asyncio.Event()

    async def slow_run() -> None:
        try:
            await asyncio.sleep(0.5)
        finally:
            completed.set()

    task = asyncio.create_task(slow_run(), name="run:integration-slow")
    rm._tasks["integration-slow"] = task
    rm._tasks_empty.clear()
    task.add_done_callback(lambda _: rm._on_task_done("integration-slow"))

    start = time.monotonic()
    await rm.drain(timeout_seconds=5.0)
    elapsed = time.monotonic() - start
    assert completed.is_set()
    assert elapsed >= 0.4
    assert "integration-slow" not in rm._tasks


@pytest.mark.asyncio
async def test_drain_timeout_force_cancels(memory_client: httpx.AsyncClient) -> None:
    app = memory_client._transport.app  # type: ignore[attr-defined]
    rm = app.state.run_manager

    cancelled_seen = asyncio.Event()

    async def long_run() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled_seen.set()
            raise

    task = asyncio.create_task(long_run(), name="run:integration-long")
    rm._tasks["integration-long"] = task
    rm._tasks_empty.clear()
    task.add_done_callback(lambda _: rm._on_task_done("integration-long"))

    await rm.drain(timeout_seconds=0.2)
    assert cancelled_seen.is_set()
    assert "integration-long" not in rm._tasks
