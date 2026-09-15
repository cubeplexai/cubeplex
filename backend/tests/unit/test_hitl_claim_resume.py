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

import time

import fakeredis.aioredis
import pytest

from cubeplex.streams.hitl_resume import (
    ClaimResumeOutcome,
    begin_resume_finalization,
    claim_resume,
    finalize_run_meta_if_claim_matches,
    resume_claim_matches,
    stale_answered_pending,
)
from cubeplex.streams.run_events import (
    create_run,
    get_active_run,
    get_run_meta,
    mark_run_stale,
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


async def test_resume_claim_matches_only_current_owner(redis):
    prefix = "test_claim_owner"
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
    await redis.hset(f"{prefix}:run_meta:v2:r1", "claim_token", "current-token")

    assert await resume_claim_matches(
        redis,
        prefix=prefix,
        run_id="r1",
        claim_token="current-token",
    )
    assert not await resume_claim_matches(
        redis,
        prefix=prefix,
        run_id="r1",
        claim_token="replaced-token",
    )


async def test_begin_finalization_reserves_claim_against_stale_recovery(redis):
    prefix = "test_begin_finalizing"
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
    meta_key = f"{prefix}:run_meta:v2:r1"
    await redis.hset(meta_key, "claim_token", "current-token")

    reserved = await begin_resume_finalization(
        redis,
        prefix=prefix,
        conversation_id="c1",
        run_id="r1",
        claim_token="current-token",
        ttl_seconds=60,
        lease_seconds=30,
    )

    assert reserved is True
    assert (await redis.hgetall(meta_key))["resume_finalizing_token"] == "current-token"
    marked = await mark_run_stale(
        redis,
        prefix=prefix,
        run_id="r1",
        conversation_id="c1",
        observed_last_event_at="2026-06-02T00:00:00+00:00",
    )
    assert marked is False


async def test_claim_rejects_stale_run_while_resume_finalization_is_reserved(redis):
    """A stale detector cannot reopen a resume while its DB cleanup is in flight."""
    prefix = "test_claim_finalizing"
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
    meta_key = f"{prefix}:run_meta:v2:r1"
    await redis.hset(
        meta_key,
        mapping={
            "claim_token": "current-token",
            "resume_finalizing_token": "current-token",
            "resume_finalizing_until": str(int(time.time()) + 30),
            "status": "stale",
        },
    )

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
    assert (await redis.hgetall(meta_key))["claim_token"] == "current-token"


async def test_expired_finalization_reservation_allows_stale_recovery(redis):
    prefix = "test_expired_finalizing"
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
    meta_key = f"{prefix}:run_meta:v2:r1"
    await redis.hset(
        meta_key,
        mapping={
            "claim_token": "abandoned-token",
            "resume_finalizing_token": "abandoned-token",
            "resume_finalizing_until": "0",
        },
    )

    marked = await mark_run_stale(
        redis,
        prefix=prefix,
        run_id="r1",
        conversation_id="c1",
        observed_last_event_at="2026-06-02T00:00:00+00:00",
    )
    assert marked is True
    assert not await redis.hexists(meta_key, "claim_token")

    finalized = await finalize_run_meta_if_claim_matches(
        redis,
        prefix=prefix,
        run_id="r1",
        claim_token="abandoned-token",
        status="completed",
    )
    assert finalized is False
    assert (await redis.hgetall(meta_key))["status"] == "stale"

    result = await claim_resume(
        redis,
        prefix=prefix,
        conversation_id="c1",
        expected_run_id="r1",
        started_at="2026-06-02T00:00:00+00:00",
        ttl_seconds=60,
    )
    assert result.outcome == ClaimResumeOutcome.OK
    assert result.claim_token != "abandoned-token"


def test_stale_answered_pending_rejects_replacement_follow_up() -> None:
    answered = type("Pending", (), {"question_id": "q-answered"})()
    follow_up = type("Pending", (), {"question_id": "q-follow-up"})()

    assert (
        stale_answered_pending(
            final_status="completed",
            loaded_pending=(answered, "run-1"),
            answered_question_id="q-answered",
            answered_run_id="run-1",
        )
        is answered
    )
    assert (
        stale_answered_pending(
            final_status="completed",
            loaded_pending=(follow_up, "run-1"),
            answered_question_id="q-answered",
            answered_run_id="run-1",
        )
        is None
    )
    assert (
        stale_answered_pending(
            final_status="completed",
            loaded_pending=(answered, "run-replacement"),
            answered_question_id="q-answered",
            answered_run_id="run-1",
        )
        is None
    )
