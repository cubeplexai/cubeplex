"""E2E tests for per-trigger Redis token-bucket rate limiter."""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from cubeplex.config import config
from cubeplex.triggers.rate_limit import allow


def _test_key_prefix() -> str:
    """Return a key prefix that falls under the autouse flush pattern.

    The conftest._flush_test_redis fixture wipes ``{base}:{env}:*`` before
    each test, so test keys must share that prefix to be cleaned up between
    runs.
    """
    base = config.get("redis.key_prefix", "cubeplex")
    env = os.getenv("ENV_FOR_DYNACONF", "development")
    return f"{base}:{env}:rl-test"


@pytest_asyncio.fixture
async def redis_client() -> Redis:  # type: ignore[misc]
    client: Redis = Redis.from_url(
        config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=False,
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_burst_then_rate_limited(redis_client: Redis) -> None:
    """First burst calls succeed; the next call is rate-limited."""
    kwargs = {
        "key_prefix": _test_key_prefix(),
        "trigger_id": "trig-burst",
        "rate_per_min": 60,
        "burst": 3,
        "now": 1000.0,
    }

    # Consume all burst tokens.
    assert await allow(redis_client, **kwargs) is True
    assert await allow(redis_client, **kwargs) is True
    assert await allow(redis_client, **kwargs) is True

    # Bucket empty — rate-limited.
    assert await allow(redis_client, **kwargs) is False


@pytest.mark.asyncio
async def test_refill_after_one_second(redis_client: Redis) -> None:
    """Advancing now by 1s refills exactly 1 token (rate_per_min=60 → 1/s)."""
    base_kwargs = {
        "key_prefix": _test_key_prefix(),
        "trigger_id": "trig-refill",
        "rate_per_min": 60,
        "burst": 3,
    }

    # Drain the bucket at t=2000.
    for _ in range(3):
        assert await allow(redis_client, **base_kwargs, now=2000.0) is True
    assert await allow(redis_client, **base_kwargs, now=2000.0) is False

    # 1 second later → 1 token refilled.
    assert await allow(redis_client, **base_kwargs, now=2001.0) is True
    # That refilled token is now consumed; bucket empty again.
    assert await allow(redis_client, **base_kwargs, now=2001.0) is False


@pytest.mark.asyncio
async def test_burst_cap_after_long_idle(redis_client: Redis) -> None:
    """Refill after a long idle is capped at burst; can't accumulate beyond it."""
    base_kwargs = {
        "key_prefix": _test_key_prefix(),
        "trigger_id": "trig-cap",
        "rate_per_min": 60,
        "burst": 3,
    }

    # Drain bucket at t=3000.
    for _ in range(3):
        assert await allow(redis_client, **base_kwargs, now=3000.0) is True
    assert await allow(redis_client, **base_kwargs, now=3000.0) is False

    # 100 seconds later → refill = 100 tokens, but capped at burst=3.
    assert await allow(redis_client, **base_kwargs, now=3100.0) is True
    assert await allow(redis_client, **base_kwargs, now=3100.0) is True
    assert await allow(redis_client, **base_kwargs, now=3100.0) is True
    assert await allow(redis_client, **base_kwargs, now=3100.0) is False
