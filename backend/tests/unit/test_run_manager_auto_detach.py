"""Tests for the auto-detach listener wired into ``_run_cubepi_path``.

The listener schedules ``agent.detach()`` exactly once when the agent
emits a ``HitlRequestEvent`` so the worker can release the run while the
pending request is durably persisted by the CheckpointedChannel.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from cubepi.agent.types import HitlRequestEvent
from cubepi.hitl.types import ApproveRequest, HitlRequest

from cubebox.streams.run_manager import _build_auto_detach_listener

pytestmark = pytest.mark.asyncio


def _make_hitl_request_event() -> HitlRequestEvent:
    req = HitlRequest(
        question_id="q1",
        thread_id="t1",
        payload=ApproveRequest(
            tool_name="execute",
            tool_call_id="tc1",
            args={"command": "ls"},
            details={"matched_pattern": "ls *"},
        ),
        created_at=1700000000.0,
    )
    return HitlRequestEvent(request=req)


async def test_auto_detach_fires_once_on_hitl_request_event() -> None:
    agent = MagicMock()
    agent.detach = AsyncMock()

    listener = _build_auto_detach_listener(agent)
    evt = _make_hitl_request_event()

    listener(evt)
    listener(evt)  # second fire — should NOT re-schedule

    # Yield so any task scheduled by create_task gets a chance to run.
    await asyncio.sleep(0)

    agent.detach.assert_called_once()
    assert listener.detached is True


async def test_auto_detach_does_not_fire_on_other_events() -> None:
    agent = MagicMock()
    agent.detach = AsyncMock()

    listener = _build_auto_detach_listener(agent)
    listener(object())

    await asyncio.sleep(0)

    agent.detach.assert_not_called()
    assert listener.detached is False


async def test_detached_flag_set_synchronously() -> None:
    """``.detached`` must flip before the scheduled ``detach()`` runs.

    T6's terminal block reads this flag after ``agent.prompt()`` returns
    to distinguish "real new pending this turn" from "stale leftover".
    The flip therefore has to be synchronous with the listener call —
    not deferred to the create_task await.
    """
    agent = MagicMock()
    agent.detach = AsyncMock()

    listener = _build_auto_detach_listener(agent)
    listener(_make_hitl_request_event())

    # NO awaits between the call above and this assertion.
    assert listener.detached is True

    # Drain so the scheduled task doesn't leak into the next test.
    await asyncio.sleep(0)
