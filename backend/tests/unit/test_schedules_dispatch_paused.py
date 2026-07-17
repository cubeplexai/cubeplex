"""Tests for `ConversationPausedError` dispatch path (T17).

When the target conversation is paused on a pending HITL request,
`dispatch_scheduled_run` must raise `ConversationPausedError` (terminal
skip) instead of `ConversationBusyError` (busy-retry burns budget).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from cubeplex.schedules.dispatch import (
    ConversationBusyError,
    ConversationPausedError,
    dispatch_scheduled_run,
)

pytestmark = pytest.mark.asyncio


def _fake_task(target_mode: str = "fixed") -> object:
    """Minimal ScheduledTask stub — only the fields dispatch reads."""
    return type(
        "TaskStub",
        (),
        {
            "target_mode": target_mode,
            "target_conversation_id": "c-1",
            "owner_user_id": "u-1",
            "org_id": "o-1",
            "workspace_id": "w-1",
            "name": "t-1",
            "prompt": "go",
        },
    )()


async def test_pending_hitl_message_raises_conversation_paused_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new RuntimeError shape from T3 contains 'pending HITL request'
    — dispatch must map it to ConversationPausedError, NOT ConversationBusyError."""

    async def _owner_member(_task: object) -> bool:
        return True

    async def _resolve(_task: object) -> str:
        return "c-1"

    monkeypatch.setattr("cubeplex.schedules.dispatch._owner_still_member", _owner_member)
    monkeypatch.setattr("cubeplex.schedules.dispatch.resolve_target", _resolve)

    rm = AsyncMock()
    rm.start_run = AsyncMock(
        side_effect=RuntimeError(
            "Conversation c-1 has a pending HITL request "
            "(question_id=q-1); answer or cancel before starting a new turn"
        )
    )

    with pytest.raises(ConversationPausedError, match="pending HITL request"):
        await dispatch_scheduled_run(task=_fake_task(), run_manager=rm)


async def test_already_active_message_still_raises_busy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression — the existing busy case must keep working unchanged.
    'already has an active run' takes the ConversationBusyError branch."""

    async def _owner_member(_task: object) -> bool:
        return True

    async def _resolve(_task: object) -> str:
        return "c-1"

    monkeypatch.setattr("cubeplex.schedules.dispatch._owner_still_member", _owner_member)
    monkeypatch.setattr("cubeplex.schedules.dispatch.resolve_target", _resolve)

    rm = AsyncMock()
    rm.start_run = AsyncMock(side_effect=RuntimeError("Conversation c-1 already has an active run"))

    with pytest.raises(ConversationBusyError):
        await dispatch_scheduled_run(task=_fake_task(), run_manager=rm)


async def test_unrelated_runtime_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrecognized RuntimeError must surface — neither paused nor busy."""

    async def _owner_member(_task: object) -> bool:
        return True

    async def _resolve(_task: object) -> str:
        return "c-1"

    monkeypatch.setattr("cubeplex.schedules.dispatch._owner_still_member", _owner_member)
    monkeypatch.setattr("cubeplex.schedules.dispatch.resolve_target", _resolve)

    rm = AsyncMock()
    rm.start_run = AsyncMock(side_effect=RuntimeError("provider timed out"))

    with pytest.raises(RuntimeError, match="provider timed out"):
        await dispatch_scheduled_run(task=_fake_task(), run_manager=rm)
