"""E2E: startup recovery for stranded runs.

Simulates a process crash leaving behind a Redis active-run key with
status=running and a cubepi_runs row with completed_at IS NULL, then
calls recover_stranded_runs() and verifies cleanup.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from redis.asyncio import Redis

from cubebox.agents.checkpointer import _build_dsn
from cubebox.streams.recovery import recover_stranded_runs


def _test_prefix() -> str:
    base = "cubebox"
    env = os.getenv("ENV_FOR_DYNACONF", "development")
    return f"{base}:{env}"


async def _plant_stranded_run(
    redis: Redis,
    prefix: str,
) -> tuple[str, str]:
    """Plant a stranded run in Redis + Postgres. Returns (conv_id, run_id)."""
    conv_id = f"conv-recovery-{uuid.uuid4().hex[:8]}"
    run_id = str(uuid.uuid4())
    stale_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()

    active_key = f"{prefix}:conversation_active_run:{conv_id}"
    meta_key = f"{prefix}:run_meta:v2:{run_id}"
    await redis.set(active_key, run_id, ex=43200)
    await redis.hset(
        meta_key,
        mapping={
            "run_id": run_id,
            "conversation_id": conv_id,
            "status": "running",
            "started_at": stale_time,
        },
    )
    await redis.expire(meta_key, 43200)

    conn = await asyncpg.connect(_build_dsn())
    try:
        await conn.execute(
            "INSERT INTO cubepi_threads (thread_id) VALUES ($1) ON CONFLICT DO NOTHING",
            conv_id,
        )
        await conn.execute(
            "INSERT INTO cubepi_runs (thread_id, run_id, claimed_at) VALUES ($1, $2, $3)",
            conv_id,
            run_id,
            datetime.now(UTC) - timedelta(minutes=10),
        )
    finally:
        await conn.close()

    return conv_id, run_id


async def _cleanup_thread(conv_id: str, run_id: str, prefix: str, redis: Redis) -> None:
    await redis.delete(
        f"{prefix}:conversation_active_run:{conv_id}",
        f"{prefix}:run_meta:v2:{run_id}",
    )
    conn = await asyncpg.connect(_build_dsn())
    try:
        await conn.execute(
            "DELETE FROM cubepi_runs WHERE thread_id = $1",
            conv_id,
        )
        await conn.execute(
            "DELETE FROM cubepi_threads WHERE thread_id = $1",
            conv_id,
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_recover_stranded_runs_clears_redis_and_stamps_db() -> None:
    from cubebox.config import config

    prefix = _test_prefix()
    redis = Redis.from_url(
        config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=True,
    )
    try:
        conv_id, run_id = await _plant_stranded_run(redis, prefix)
        try:
            active_key = f"{prefix}:conversation_active_run:{conv_id}"
            assert await redis.exists(active_key)

            count = await recover_stranded_runs(redis, prefix=prefix)
            assert count >= 1

            assert not await redis.exists(active_key)

            meta_key = f"{prefix}:run_meta:v2:{run_id}"
            status = await redis.hget(meta_key, "status")
            assert status == "stale"

            conn = await asyncpg.connect(_build_dsn())
            try:
                row = await conn.fetchrow(
                    "SELECT completed_at FROM cubepi_runs WHERE thread_id = $1 AND run_id = $2",
                    conv_id,
                    run_id,
                )
                assert row is not None
                assert row["completed_at"] is not None
            finally:
                await conn.close()
        finally:
            await _cleanup_thread(conv_id, run_id, prefix, redis)
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_recover_noop_when_no_stranded_runs() -> None:
    from cubebox.config import config

    prefix = _test_prefix()
    redis = Redis.from_url(
        config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=True,
    )
    try:
        count = await recover_stranded_runs(redis, prefix=prefix)
        assert count == 0
    finally:
        await redis.aclose()
