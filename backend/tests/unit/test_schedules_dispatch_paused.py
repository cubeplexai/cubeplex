"""Tests for `ConversationPausedError` dispatch path (T17).

When the target conversation is paused on a pending HITL request,
`dispatch_scheduled_run` must raise `ConversationPausedError` (terminal
skip) instead of `ConversationBusyError` (busy-retry burns budget).
"""

from __future__ import annotations

import pytest

from cubeplex.schedules.dispatch import (
    ConversationBusyError,
    ConversationPausedError,
    raise_scheduled_start_error,
)

pytestmark = pytest.mark.asyncio


async def test_pending_hitl_message_raises_conversation_paused_error() -> None:
    """The new RuntimeError shape from T3 contains 'pending HITL request'
    — dispatch must map it to ConversationPausedError, NOT ConversationBusyError."""

    with pytest.raises(ConversationPausedError, match="pending HITL request"):
        raise_scheduled_start_error(
            "fixed",
            RuntimeError(
                "Conversation c-1 has a pending HITL request "
                "(question_id=q-1); answer or cancel before starting a new turn"
            ),
        )


async def test_already_active_message_still_raises_busy_error() -> None:
    """Regression — the existing busy case must keep working unchanged.
    'already has an active run' takes the ConversationBusyError branch."""

    with pytest.raises(ConversationBusyError):
        raise_scheduled_start_error(
            "fixed", RuntimeError("Conversation c-1 already has an active run")
        )


async def test_unrelated_runtime_error_propagates() -> None:
    """Unrecognized RuntimeError must surface — neither paused nor busy."""

    with pytest.raises(RuntimeError, match="provider timed out"):
        raise_scheduled_start_error("fixed", RuntimeError("provider timed out"))
