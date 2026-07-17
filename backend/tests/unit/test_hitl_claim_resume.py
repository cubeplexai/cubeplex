"""Tests for ``claim_resume`` — the single-flight resume CAS.

``claim_resume`` is the Lua-backed handoff that lets a worker take over a
paused (or stale, or TTL-expired) HITL conversation. The CAS must:

1. Succeed when meta exists with status ``paused_hitl`` or ``stale``
   (flipping status back to ``running`` and stamping a fresh claim_token).
2. Refuse when meta exists with status ``running`` (someone already owns it).
3. Refuse when the active-run pointer disagrees with ``expected_run_id``.
4. Rebuild the meta hash from scratch when it has aged out of Redis,
   writing ALL fields ``_meta_from_hash`` requires. This is the
   load-bearing case: if the rebuild branch drops a field, the next
   ``get_active_run`` raises ``KeyError`` on the half-built hash.
5. Refuse when meta exists with a terminal status (completed / cancelled
   / errored) — the CAS should never resurrect a finished conversation.
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from cubeplex.streams.hitl_resume import (
    ClaimResumeOutcome,
    claim_resume,
)
from cubeplex.streams.run_events import (
    create_run,
    get_active_run,
    get_run_meta,
    update_run_meta,
)


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def test_claim_from_paused_hitl_succeeds(redis):
    """Happy path: paused conversation → claim_resume flips it back to running."""
    prefix = "test_claim_paused_ok"
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
    paused = await update_run_meta(redis, prefix=prefix, run_id="r1", status="paused_hitl")
    assert paused is not None and paused.status == "paused_hitl"

    result = await claim_resume(
        redis,
        prefix=prefix,
        conversation_id="c1",
        expected_run_id="r1",
        started_at="2026-06-02T00:00:00+00:00",
        ttl_seconds=60,
    )
    assert result.outcome == ClaimResumeOutcome.OK
    assert result.claim_token is not None
    assert len(result.claim_token) > 0

    meta = await get_active_run(redis, prefix=prefix, conversation_id="c1")
    assert meta is not None
    assert meta.run_id == "r1"
    assert meta.status == "running"

    # And the claim_token field was written to the hash.
    raw = await redis.hgetall(f"{prefix}:run_meta:v2:r1")
    assert raw["claim_token"] == result.claim_token


async def test_claim_rejects_when_already_running(redis):
    """A running conversation can't be claimed — another worker still owns it."""
    prefix = "test_claim_already_running"
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

    before = await redis.hgetall(f"{prefix}:run_meta:v2:r1")

    result = await claim_resume(
        redis,
        prefix=prefix,
        conversation_id="c1",
        expected_run_id="r1",
        started_at="2026-06-02T00:00:00+00:00",
        ttl_seconds=60,
    )
    assert result.outcome == ClaimResumeOutcome.ALREADY_RUNNING
    assert result.claim_token is None

    # Meta untouched — no claim_token stamped, status still running.
    after = await redis.hgetall(f"{prefix}:run_meta:v2:r1")
    assert after == before
    assert "claim_token" not in after


async def test_claim_conflict_when_active_pointer_differs(redis):
    """The CAS must refuse when the active-run pointer disagrees."""
    prefix = "test_claim_conflict_pointer"
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

    result = await claim_resume(
        redis,
        prefix=prefix,
        conversation_id="c1",
        expected_run_id="r0",  # not r1
        started_at="2026-06-02T00:00:00+00:00",
        ttl_seconds=60,
    )
    assert result.outcome == ClaimResumeOutcome.CONFLICT
    assert result.claim_token is None

    # Active key still points at r1, untouched.
    current = await redis.get(f"{prefix}:conversation_active_run:c1")
    assert current == "r1"
    # r0's meta key was never created.
    r0_meta = await get_run_meta(redis, prefix=prefix, run_id="r0")
    assert r0_meta is None


async def test_claim_rebuild_when_meta_expired(redis):
    """THE critical test: long-pause TTL recovery rebuilds a complete meta.

    If the rebuild branch in the Lua script forgets any required field,
    ``get_active_run`` will KeyError on the next read. The fact that this
    test returns a well-formed RunMeta is the proof that the rebuild
    branch wrote all of (run_id, conversation_id, status, started_at).
    """
    prefix = "test_claim_rebuild"
    # Clean Redis: no active key, no meta. Simulates meta TTL aged out.

    result = await claim_resume(
        redis,
        prefix=prefix,
        conversation_id="c1",
        expected_run_id="r1",
        started_at="2026-06-02T00:00:00+00:00",
        ttl_seconds=60,
    )
    assert result.outcome == ClaimResumeOutcome.OK
    assert result.claim_token is not None

    meta = await get_active_run(redis, prefix=prefix, conversation_id="c1")
    assert meta is not None
    assert meta.run_id == "r1"
    assert meta.conversation_id == "c1"
    assert meta.status == "running"
    assert meta.started_at == "2026-06-02T00:00:00+00:00"


async def test_claim_conflict_on_terminal_status(redis):
    """The CAS must refuse to resurrect a completed/cancelled/errored run."""
    prefix = "test_claim_conflict_terminal"
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
    completed = await update_run_meta(redis, prefix=prefix, run_id="r1", status="completed")
    assert completed is not None and completed.status == "completed"

    result = await claim_resume(
        redis,
        prefix=prefix,
        conversation_id="c1",
        expected_run_id="r1",
        started_at="2026-06-02T00:00:00+00:00",
        ttl_seconds=60,
    )
    assert result.outcome == ClaimResumeOutcome.CONFLICT
    assert result.claim_token is None

    # Status stays 'completed'; no claim_token written.
    raw = await redis.hgetall(f"{prefix}:run_meta:v2:r1")
    assert raw["status"] == "completed"
    assert "claim_token" not in raw
