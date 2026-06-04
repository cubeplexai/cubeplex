"""RunMeta carries classified error fields when a run fails."""

from __future__ import annotations

import pytest
from fakeredis.aioredis import FakeRedis

from cubebox.streams.run_events import create_run, get_run_meta, update_run_meta


@pytest.mark.asyncio
async def test_run_meta_round_trips_error_fields() -> None:
    redis = FakeRedis(decode_responses=True)
    prefix = "cb-test"
    await create_run(
        redis,
        prefix=prefix,
        run_id="r1",
        conversation_id="c1",
        status="running",
        started_at="2026-06-04T10:00:00+00:00",
        ttl_seconds=600,
    )

    await update_run_meta(
        redis,
        prefix=prefix,
        run_id="r1",
        status="errored",
        error_code="context_length_exceeded",
        error_params='{"model":"kimi-k2.6","tokens_in":262014,"context_window":256000}',
        error_message="Conversation exceeds the model's context window.",
    )

    meta = await get_run_meta(redis, prefix=prefix, run_id="r1")
    assert meta is not None
    assert meta.error_code == "context_length_exceeded"
    assert (meta.error_message or "").startswith("Conversation exceeds")
    assert '"tokens_in":262014' in (meta.error_params or "")
