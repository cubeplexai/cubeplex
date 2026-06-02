"""Tests for the ``paused_hitl`` run status — added by durable HITL feature.

``paused_hitl`` runs are alive-but-detached: a HITL pending request has been
persisted, the worker has released, and the conversation is waiting for the
user's answer. Two things must hold for the rest of the system to stay
correct:

1. The stale-run sweeper must NOT mark them stale — paused runs have no
   freshness expectation; they can sit for hours or days.
2. A concurrent ``create_run`` that falls through to the force-claim
   recovery path must NOT silently overwrite the paused conversation —
   that would lose the pending request and orphan the user's turn.
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from cubebox.streams.run_events import (
    RunMeta,
    create_run,
    get_active_run,
    get_run_meta,
    is_stale_meta,
    update_run_meta,
)


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def test_paused_hitl_status_round_trips(redis):
    """update_run_meta accepts paused_hitl from running; get_active_run reads it back."""
    prefix = "test_paused_hitl_round_trip"
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

    updated = await update_run_meta(
        redis,
        prefix=prefix,
        run_id="r1",
        status="paused_hitl",
    )
    assert updated is not None
    assert updated.status == "paused_hitl"

    meta = await get_active_run(redis, prefix=prefix, conversation_id="c1")
    assert meta is not None
    assert meta.run_id == "r1"
    assert meta.status == "paused_hitl"


def test_paused_hitl_is_not_stale():
    """Sweeper logic: paused_hitl has no freshness expectation."""
    meta = RunMeta(
        run_id="r1",
        conversation_id="c1",
        status="paused_hitl",
        started_at="2026-06-02T00:00:00+00:00",
        user_message="hi",
        first_event_id=None,
        last_event_id=None,
        last_event_at="2020-01-01T00:00:00+00:00",  # ancient
    )
    assert is_stale_meta(meta, threshold_seconds=10) is False


async def test_force_claim_stale_protects_paused_hitl(redis):
    """Load-bearing safety: a concurrent create_run that falls back to
    ``_FORCE_CLAIM_STALE_LUA`` must NOT overwrite a paused conversation.

    Without the guard, the paused run silently disappears from Redis and
    the user's pending HITL turn is orphaned.

    ``create_run`` signals "couldn't claim" by returning ``None`` (see
    cubebox/streams/run_events.py: returns ``None`` on both the primary
    and force-claim failure branches).
    """
    prefix = "test_force_claim_paused"
    first = await create_run(
        redis,
        prefix=prefix,
        run_id="r1",
        conversation_id="c1",
        status="running",
        started_at="2026-06-02T00:00:00+00:00",
        user_message="hi",
        ttl_seconds=60,
    )
    assert first is not None

    paused = await update_run_meta(
        redis,
        prefix=prefix,
        run_id="r1",
        status="paused_hitl",
    )
    assert paused is not None
    assert paused.status == "paused_hitl"

    # A concurrent create_run attempt for a new run_id must NOT take over.
    # The active-run key still points at r1, so _CLAIM_ACTIVE_LUA fails;
    # the force-claim fallback then sees r1's status == 'paused_hitl' and
    # (post-fix) must refuse to claim, returning None.
    result = await create_run(
        redis,
        prefix=prefix,
        run_id="r2",
        conversation_id="c1",
        status="running",
        started_at="2026-06-02T00:01:00+00:00",
        user_message="next",
        ttl_seconds=60,
    )
    assert result is None, "force-claim should not overwrite a paused_hitl run"

    # The original paused run must survive intact.
    meta = await get_active_run(redis, prefix=prefix, conversation_id="c1")
    assert meta is not None, "paused conversation was overwritten by force-claim"
    assert meta.run_id == "r1"
    assert meta.status == "paused_hitl"

    # And r2's meta hash must not have been written.
    r2_meta = await get_run_meta(redis, prefix=prefix, run_id="r2")
    assert r2_meta is None
