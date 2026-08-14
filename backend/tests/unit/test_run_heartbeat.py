"""Unit tests: silent last_event_at heartbeat during long tool bodies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest

from cubeplex.streams.run_events import (
    create_run,
    get_active_run,
    get_run_meta,
    iter_run_events,
    mark_run_stale,
    touch_run_heartbeat,
)


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_touch_run_heartbeat_updates_last_event_at_without_events(redis) -> None:
    prefix = "test_run_hb"
    run_id = "r-hb"
    conv_id = "c-hb"
    created = await create_run(
        redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )
    assert created is not None
    old = (datetime.now(UTC) - timedelta(seconds=400)).isoformat()
    await redis.hset(f"{prefix}:run_meta:v2:{run_id}", "last_event_at", old)

    await touch_run_heartbeat(
        redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        ttl_seconds=60,
    )

    meta = await get_run_meta(redis, prefix=prefix, run_id=run_id)
    assert meta is not None
    assert meta.status == "running"
    assert meta.last_event_at is not None
    assert meta.last_event_at != old
    assert await iter_run_events(redis, prefix=prefix, run_id=run_id) == []


@pytest.mark.asyncio
async def test_in_flight_tool_heartbeat_starts_and_stops() -> None:
    import asyncio

    from cubepi.agent.types import ToolExecutionEndEvent, ToolExecutionStartEvent

    from cubeplex.streams.run_manager import _InFlightToolHeartbeat

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    prefix = "test_ifhb"
    run_id = "r1"
    conv_id = "c1"
    await create_run(
        redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )
    old = (datetime.now(UTC) - timedelta(seconds=400)).isoformat()
    await redis.hset(f"{prefix}:run_meta:v2:{run_id}", "last_event_at", old)

    hb = _InFlightToolHeartbeat(
        redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        ttl_seconds=60,
        interval_seconds=0.05,
    )
    hb.observe(
        ToolExecutionStartEvent(
            tool_call_id="c1",
            tool_name="execute",
            args={"command": "pip install foo"},
        )
    )
    await asyncio.sleep(0.12)
    meta = await get_run_meta(redis, prefix=prefix, run_id=run_id)
    assert meta is not None
    assert meta.last_event_at != old
    hb.observe(ToolExecutionEndEvent(tool_call_id="c1", tool_name="execute", is_error=False))
    assert hb._task is None or hb._task.done()
    hb.stop()


@pytest.mark.asyncio
async def test_mark_run_stale_cas_rejects_refreshed_heartbeat(redis) -> None:
    prefix = "test_stale_cas"
    run_id = "r-cas-hb"
    conv_id = "c-cas-hb"
    started = datetime.now(UTC).isoformat()
    created = await create_run(
        redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=started,
        ttl_seconds=60,
    )
    assert created is not None
    stale_at = (datetime.now(UTC) - timedelta(seconds=400)).isoformat()
    await redis.hset(f"{prefix}:run_meta:v2:{run_id}", "last_event_at", stale_at)

    await touch_run_heartbeat(
        redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        ttl_seconds=60,
    )

    marked = await mark_run_stale(
        redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        observed_last_event_at=stale_at,
    )
    assert marked is False
    meta = await get_run_meta(redis, prefix=prefix, run_id=run_id)
    assert meta is not None
    assert meta.status == "running"
    active = await get_active_run(redis, prefix=prefix, conversation_id=conv_id)
    assert active is not None
    assert active.run_id == run_id


@pytest.mark.asyncio
async def test_mark_run_stale_cas_accepts_matching_timestamp(redis) -> None:
    prefix = "test_stale_ok"
    run_id = "r-stale-ok"
    conv_id = "c-stale-ok"
    created = await create_run(
        redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )
    assert created is not None
    stale_at = (datetime.now(UTC) - timedelta(seconds=400)).isoformat()
    await redis.hset(f"{prefix}:run_meta:v2:{run_id}", "last_event_at", stale_at)

    marked = await mark_run_stale(
        redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        observed_last_event_at=stale_at,
    )
    assert marked is True
    meta = await get_run_meta(redis, prefix=prefix, run_id=run_id)
    assert meta is not None
    assert meta.status == "stale"
    assert await get_active_run(redis, prefix=prefix, conversation_id=conv_id) is None
