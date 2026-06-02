"""Tests for ``finalize_run_meta_if_claim_matches`` — the CAS guard that the
respond path uses to write its terminal status only if our claim still owns
the run meta row.

This guards a future race where two flows could land in the respond path's
``finally`` block at once. Whichever wrote ``claim_token`` last wins the row;
the loser's terminal write is a no-op.

See ``docs/dev/specs/2026-06-02-hitl-checkpointed-respond-design.md`` §5.
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from cubebox.streams.hitl_resume import finalize_run_meta_if_claim_matches
from cubebox.streams.run_events import (
    create_run,
    get_run_meta,
)


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def test_finalize_writes_status_when_token_matches(redis):
    """Happy path: caller's claim_token matches → status is written."""
    prefix = "test_finalize_match"
    created = await create_run(
        redis,
        prefix=prefix,
        run_id="r1",
        conversation_id="c1",
        status="running",
        started_at="2026-06-02T00:00:00+00:00",
        user_message="hi",
        ttl_seconds=60,
    )
    assert created is not None
    # Stamp a claim_token on the meta row (mirrors what claim_resume does).
    await redis.hset(f"{prefix}:run_meta:v2:r1", "claim_token", "tok1")

    ok = await finalize_run_meta_if_claim_matches(
        redis,
        prefix=prefix,
        run_id="r1",
        claim_token="tok1",
        status="completed",
    )
    assert ok is True

    meta = await get_run_meta(redis, prefix=prefix, run_id="r1")
    assert meta is not None
    assert meta.status == "completed"


async def test_finalize_no_op_when_token_mismatches(redis):
    """Mismatch path: another flow stamped a different claim_token → no-op."""
    prefix = "test_finalize_mismatch"
    created = await create_run(
        redis,
        prefix=prefix,
        run_id="r1",
        conversation_id="c1",
        status="running",
        started_at="2026-06-02T00:00:00+00:00",
        user_message="hi",
        ttl_seconds=60,
    )
    assert created is not None
    await redis.hset(f"{prefix}:run_meta:v2:r1", "claim_token", "tok1")
    before = await redis.hgetall(f"{prefix}:run_meta:v2:r1")

    ok = await finalize_run_meta_if_claim_matches(
        redis,
        prefix=prefix,
        run_id="r1",
        claim_token="tok-other",
        status="completed",
    )
    assert ok is False

    # Meta untouched — status stays whatever it was, claim_token stays tok1.
    after = await redis.hgetall(f"{prefix}:run_meta:v2:r1")
    assert after == before
    assert after["claim_token"] == "tok1"
    assert after["status"] == "running"


async def test_finalize_no_op_when_meta_missing(redis):
    """Missing-meta path: HGET returns nil ≠ token → no-op, no crash."""
    prefix = "test_finalize_missing"
    # Clean Redis: no meta key at all.

    ok = await finalize_run_meta_if_claim_matches(
        redis,
        prefix=prefix,
        run_id="r-ghost",
        claim_token="tok1",
        status="completed",
    )
    assert ok is False

    # No meta was conjured into existence by the call.
    meta = await get_run_meta(redis, prefix=prefix, run_id="r-ghost")
    assert meta is None
