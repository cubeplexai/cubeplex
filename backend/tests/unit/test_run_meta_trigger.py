"""RunMeta persists trigger for HITL resume write-gating."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from cubeplex.streams.run_events import create_run, get_run_meta

pytestmark = pytest.mark.asyncio


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def test_create_run_persists_trigger(redis) -> None:
    meta = await create_run(
        redis,
        prefix="test_trigger",
        run_id="run_sched_1",
        conversation_id="conv_1",
        status="running",
        started_at="2026-07-23T00:00:00+00:00",
        ttl_seconds=60,
        trigger="schedule",
    )
    assert meta is not None
    assert meta.trigger == "schedule"

    loaded = await get_run_meta(redis, prefix="test_trigger", run_id="run_sched_1")
    assert loaded is not None
    assert loaded.trigger == "schedule"


async def test_create_run_without_trigger_leaves_none(redis) -> None:
    meta = await create_run(
        redis,
        prefix="test_trigger",
        run_id="run_int_1",
        conversation_id="conv_2",
        status="running",
        started_at="2026-07-23T00:00:00+00:00",
        ttl_seconds=60,
    )
    assert meta is not None
    assert meta.trigger is None
    loaded = await get_run_meta(redis, prefix="test_trigger", run_id="run_int_1")
    assert loaded is not None
    assert loaded.trigger is None
