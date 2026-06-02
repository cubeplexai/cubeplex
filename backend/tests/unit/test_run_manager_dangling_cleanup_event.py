"""Tests for _emit_synthetic_resolved — the dangling-pending cleanup
branch's frontend-notification mechanism. See spec §6."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cubebox.agents.schemas import AskUserResolvedEvent, SandboxConfirmResolvedEvent
from cubebox.streams.run_manager import _emit_synthetic_resolved

pytestmark = pytest.mark.asyncio


async def test_dangling_sandbox_cleanup_emits_typed_resolved_event():
    publish = AsyncMock()
    pending = MagicMock()
    pending.payload.kind = "approve"
    pending.payload.tool_call_id = "tc1"
    await _emit_synthetic_resolved(publish, pending, "q1")
    publish.assert_awaited_once()
    event, agent_key = publish.await_args.args
    assert isinstance(event, SandboxConfirmResolvedEvent)
    assert event.data["question_id"] == "q1"
    assert event.data["tool_call_id"] == "tc1"
    assert event.data["decision"] == "policy_overridden"
    assert event.data["cancelled"] is False
    assert event.data["timed_out"] is False
    assert event.data["reason"] == "org sandbox policy changed during pause"
    assert agent_key is None


async def test_dangling_ask_cleanup_emits_typed_resolved_event():
    publish = AsyncMock()
    pending = MagicMock()
    pending.payload.kind = "ask"
    await _emit_synthetic_resolved(publish, pending, "q1")
    publish.assert_awaited_once()
    event = publish.await_args.args[0]
    assert isinstance(event, AskUserResolvedEvent)
    assert event.data["question_id"] == "q1"
    assert event.data["answers"] is None
    assert event.data["cancelled"] is True
    assert event.data["timed_out"] is False
    assert event.data["reason"] == "policy_overridden"


async def test_dangling_cleanup_raises_on_unknown_kind():
    """Future cubepi kind (e.g. 'confirm') must surface loudly, not silently
    drop the synthetic event — otherwise the frontend card sticks."""
    publish = AsyncMock()
    pending = MagicMock()
    pending.payload.kind = "confirm"  # cubepi ConfirmRequest, not used by cubebox today
    with pytest.raises(ValueError, match="unhandled HITL kind"):
        await _emit_synthetic_resolved(publish, pending, "q1")
