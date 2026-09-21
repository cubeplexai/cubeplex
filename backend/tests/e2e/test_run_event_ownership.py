"""A stale worker must not mutate Redis after its slot or attempt is replaced."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from cubeplex.config import config
from cubeplex.streams.hitl_resume import (
    begin_resume_finalization,
    finalize_run_meta_if_claim_matches,
)
from cubeplex.streams.run_events import (
    RunClaimLost,
    _active_run_key,
    _run_events_key,
    _run_meta_key,
    append_run_event,
    clear_active_run,
    clear_conversation_last_error,
    create_run,
    expire_run_data,
    get_conversation_last_error,
    run_claim_matches,
    set_conversation_last_error,
    touch_run_heartbeat,
    update_run_meta,
)


@pytest_asyncio.fixture
async def owned_redis() -> AsyncIterator[tuple[Redis, str]]:
    redis = Redis.from_url(config.get("redis.url"), decode_responses=True)
    prefix = f"test-run-owner:{uuid4()}"
    try:
        yield redis, prefix
    finally:
        keys = [key async for key in redis.scan_iter(match=f"{prefix}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.parametrize("loss", ["token", "slot", "missing_meta", "missing_active"])
async def test_lost_owner_cannot_append_finalize_heartbeat_or_release(
    owned_redis: tuple[Redis, str], loss: str
) -> None:
    redis, prefix = owned_redis
    run_id = str(uuid4())
    conversation_id = str(uuid4())
    assert await create_run(
        redis,
        prefix=prefix,
        conversation_id=conversation_id,
        run_id=run_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
        claim_token="original",
    )
    kwargs = {
        "prefix": prefix,
        "conversation_id": conversation_id,
        "run_id": run_id,
        "claim_token": "original",
    }
    assert await run_claim_matches(redis, **kwargs)
    await append_run_event(redis, **kwargs, payload={"type": "status"}, ttl_seconds=60, maxlen=100)
    await set_conversation_last_error(
        redis,
        **kwargs,
        error_code="original",
        error_params="{}",
        error_message="original error",
        ttl_seconds=60,
    )
    meta_key = _run_meta_key(prefix, run_id)
    active_key = _active_run_key(prefix, conversation_id)
    stream_key = _run_events_key(prefix, run_id)
    if loss == "token":
        await redis.hset(meta_key, "claim_token", "replacement")
    elif loss == "slot":
        await clear_active_run(redis, **kwargs)
        assert await create_run(
            redis,
            prefix=prefix,
            conversation_id=conversation_id,
            run_id=str(uuid4()),
            status="running",
            started_at=datetime.now(UTC).isoformat(),
            ttl_seconds=60,
            claim_token="replacement",
        )
    elif loss == "missing_meta":
        await redis.delete(meta_key)
    else:
        await redis.delete(active_key)
    original_meta = await redis.hgetall(meta_key)
    original_events = await redis.xrange(stream_key)
    original_active = await redis.get(active_key)
    meta_ttl = await redis.pttl(meta_key)
    active_ttl = await redis.pttl(active_key)
    original_error = await get_conversation_last_error(
        redis, prefix=prefix, conversation_id=conversation_id
    )
    assert not await run_claim_matches(redis, **kwargs)
    assert not await begin_resume_finalization(redis, **kwargs, ttl_seconds=60, lease_seconds=60)
    assert not await finalize_run_meta_if_claim_matches(redis, **kwargs, status="completed")

    with pytest.raises(RunClaimLost):
        await append_run_event(
            redis, **kwargs, payload={"type": "done"}, ttl_seconds=3600, maxlen=100
        )
    with pytest.raises(RunClaimLost):
        await update_run_meta(redis, **kwargs, status="completed", error_message="stale writer")
    with pytest.raises(RunClaimLost):
        await touch_run_heartbeat(redis, **kwargs, ttl_seconds=3600)
    with pytest.raises(RunClaimLost):
        await set_conversation_last_error(
            redis,
            **kwargs,
            error_code="stale worker",
            error_params="{}",
            error_message="stale worker",
            ttl_seconds=3600,
        )
    await clear_conversation_last_error(redis, **kwargs)
    await clear_active_run(redis, **kwargs)
    if loss == "token":
        await expire_run_data(
            redis, prefix=prefix, run_id=run_id, claim_token="original", ttl_seconds=1
        )
        assert await redis.pttl(meta_key) > 30_000
    assert await redis.hgetall(meta_key) == original_meta
    assert await redis.xrange(stream_key) == original_events
    assert await redis.get(active_key) == original_active
    assert (
        await get_conversation_last_error(redis, prefix=prefix, conversation_id=conversation_id)
        == original_error
    )
    assert await redis.pttl(meta_key) <= meta_ttl
    assert await redis.pttl(active_key) <= active_ttl


async def test_finalization_lease_keeps_its_coordination_keys_alive(
    owned_redis: tuple[Redis, str],
) -> None:
    redis, prefix = owned_redis
    run_id = str(uuid4())
    conversation_id = str(uuid4())
    assert await create_run(
        redis,
        prefix=prefix,
        conversation_id=conversation_id,
        run_id=run_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
        claim_token="owner",
    )
    assert await begin_resume_finalization(
        redis,
        prefix=prefix,
        conversation_id=conversation_id,
        run_id=run_id,
        claim_token="owner",
        ttl_seconds=1,
        lease_seconds=60,
    )
    assert await redis.pttl(_run_meta_key(prefix, run_id)) > 59_000
    assert await redis.pttl(_active_run_key(prefix, conversation_id)) > 59_000


@pytest.mark.parametrize("path", ["prompt", "respond"])
@pytest.mark.parametrize("cleanup", ["finished", "expired"])
async def test_terminal_claim_is_kept_until_cleanup_or_lease_expiry(
    owned_redis: tuple[Redis, str], path: str, cleanup: str
) -> None:
    redis, prefix = owned_redis
    conversation_id, run_id, next_run_id = (str(uuid4()) for _ in range(3))
    identity = {
        "prefix": prefix,
        "conversation_id": conversation_id,
        "run_id": run_id,
        "claim_token": "owner",
    }
    assert await create_run(
        redis,
        **identity,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )
    assert await begin_resume_finalization(redis, **identity, ttl_seconds=60, lease_seconds=60)
    if path == "prompt":
        await update_run_meta(redis, **identity, status="completed")
    else:
        assert await finalize_run_meta_if_claim_matches(redis, **identity, status="completed")

    async def next_send() -> bool:
        return await create_run(
            redis,
            prefix=prefix,
            conversation_id=conversation_id,
            run_id=next_run_id,
            claim_token="next",
            status="running",
            started_at=datetime.now(UTC).isoformat(),
            ttl_seconds=60,
        )

    assert not await next_send()
    if cleanup == "finished":
        await clear_active_run(redis, **identity)
        assert not await redis.hexists(_run_meta_key(prefix, run_id), "resume_finalizing_token")
    else:
        await redis.hset(_run_meta_key(prefix, run_id), "resume_finalizing_until", "0")
    assert await next_send()
    assert not await run_claim_matches(redis, **identity)
