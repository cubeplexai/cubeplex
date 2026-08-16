"""User cancel is a run status, not an ErrorEvent.

If this regresses, the chat UI paints Stop as "Reply failed / Run cancelled".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.streams.run_manager import RunManager

pytestmark = pytest.mark.asyncio


def _make_rm() -> RunManager:
    return RunManager(
        app=MagicMock(),
        redis=MagicMock(),
        key_prefix="test_user_cancel",
        run_event_ttl_seconds=60,
    )


async def test_user_cancel_does_not_publish_an_error_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rm = _make_rm()
    rm._append_error = AsyncMock()

    monkeypatch.setattr(
        "cubeplex.streams.run_manager.update_run_meta",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "cubeplex.schedules.completion_hook.record_scheduled_run_terminal_state",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "cubeplex.streams.run_manager._repair_dangling_tool_calls",
        AsyncMock(),
    )

    await rm._record_user_cancel(run_id="run-1", conversation_id="conv-1")

    rm._append_error.assert_not_awaited()
    rm._append_error.assert_not_called()
