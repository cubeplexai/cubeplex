"""E2E tests for graceful restart drain + stale-run detection."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from cubebox.config import config as _cubebox_config
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
    start = asyncio.get_event_loop().time()
    await rm.drain(timeout_seconds=10.0)
    elapsed = asyncio.get_event_loop().time() - start
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

    start = asyncio.get_event_loop().time()
    await rm.drain(timeout_seconds=5.0)
    elapsed = asyncio.get_event_loop().time() - start
    assert 0.25 < elapsed < 1.5
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
    assert task.cancelled() or task.done()
