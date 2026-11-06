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


def _patch_cancel_collaborators(
    monkeypatch: pytest.MonkeyPatch,
    rm: RunManager,
    *,
    marker: AsyncMock | None = None,
) -> AsyncMock:
    rm._append_error = AsyncMock()
    update = AsyncMock()
    monkeypatch.setattr("cubeplex.streams.run_manager.update_run_meta", update)
    monkeypatch.setattr(
        "cubeplex.schedules.completion_hook.record_scheduled_run_terminal_state",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "cubeplex.streams.run_manager._repair_dangling_tool_calls",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "cubeplex.streams.user_stop.mark_user_stopped_run",
        marker if marker is not None else AsyncMock(),
    )
    return update


async def test_user_cancel_marks_the_run_and_does_not_publish_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rm = _make_rm()
    marker = AsyncMock()
    update = _patch_cancel_collaborators(monkeypatch, rm, marker=marker)

    await rm._record_user_cancel(run_id="run-1", conversation_id="conv-1")

    marker.assert_awaited_once_with("conv-1", "run-1")
    assert update.await_args is not None
    assert update.await_args.kwargs["status"] == "cancelled"
    rm._append_error.assert_not_awaited()
    rm._append_error.assert_not_called()


async def test_user_cancel_still_cancels_when_the_marker_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rm = _make_rm()
    marker = AsyncMock(side_effect=RuntimeError("checkpoint down"))
    update = _patch_cancel_collaborators(monkeypatch, rm, marker=marker)

    await rm._record_user_cancel(run_id="run-1", conversation_id="conv-1")

    marker.assert_awaited_once_with("conv-1", "run-1")
    update.assert_awaited()
    rm._append_error.assert_not_called()


async def test_user_cancel_does_not_relabel_a_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rm = _make_rm()
    ctx = MagicMock()
    ctx.execution.attempt_id = "attempt-1"
    monkeypatch.setattr(rm, "_owned_terminal_status", AsyncMock(return_value="failed"))
    begin = AsyncMock()
    monkeypatch.setattr(rm, "_begin_execution_finalization", begin)
    marker = AsyncMock()
    update = _patch_cancel_collaborators(monkeypatch, rm, marker=marker)

    await rm._record_user_cancel(run_id="run-1", conversation_id="conv-1", ctx=ctx)

    marker.assert_not_awaited()
    update.assert_not_awaited()
    begin.assert_not_awaited()


async def test_user_cancel_retries_the_marker_after_status_was_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rm = _make_rm()
    ctx = MagicMock()
    ctx.execution.attempt_id = "attempt-1"
    monkeypatch.setattr(rm, "_owned_terminal_status", AsyncMock(return_value="cancelled"))
    marker = AsyncMock()
    update = _patch_cancel_collaborators(monkeypatch, rm, marker=marker)

    await rm._record_user_cancel(run_id="run-1", conversation_id="conv-1", ctx=ctx)

    marker.assert_awaited_once_with("conv-1", "run-1")
    update.assert_not_awaited()
    rm._append_error.assert_not_called()
