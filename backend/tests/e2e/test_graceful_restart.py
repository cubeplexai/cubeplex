"""E2E tests for graceful restart drain + stale-run detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from cubebox.config import config as _cubebox_config
from cubebox.streams.run_events import (
    append_run_event,
    create_run,
    get_run_meta,
)

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
