from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cubeloop.providers.base import TextContent, ToolResultMessage

from cubeplex.models import BackgroundTask
from cubeplex.services.background_task_coordinator import resolve_foreground_checkpoint
from cubeplex.services.background_task_lifecycle import ForegroundResultEvidence


def _task() -> BackgroundTask:
    return BackgroundTask(
        org_id="org-test",
        workspace_id="ws-test",
        conversation_id="conv-test",
        admission_id="cea-test",
        kind="command",
        originating_run_id="run-original",
        tool_call_id="tool-original",
        started_by_user_id="user-test",
        execution_generation=0,
    )


class FakeCheckpointer:
    def __init__(self, *, messages: list[object], pending_run_id: str | None) -> None:
        self.messages = messages
        self.pending_run_id = pending_run_id
        self.mark_run_complete = AsyncMock()

    async def load(self, conversation_id: str) -> SimpleNamespace:
        assert conversation_id == "conv-test"
        return SimpleNamespace(messages=self.messages)

    async def load_pending_run_id(self, conversation_id: str) -> str | None:
        assert conversation_id == "conv-test"
        return self.pending_run_id


@pytest.mark.asyncio
async def test_checkpointed_foreground_result_wins_without_fencing() -> None:
    result = ToolResultMessage(
        tool_call_id="tool-original",
        tool_name="execute",
        content=[TextContent(text="done")],
        run_id="run-original",
    )
    checkpointer = FakeCheckpointer(messages=[result], pending_run_id="run-original")

    recovered = await resolve_foreground_checkpoint(
        _task(),
        redis=object(),
        redis_key_prefix="test",
        checkpointer=checkpointer,
        load_run_meta=AsyncMock(return_value=SimpleNamespace(status="running")),
    )

    assert recovered == ForegroundResultEvidence(
        run_id="run-original", tool_call_id="tool-original", agent_id=None
    )
    checkpointer.mark_run_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_foreground_without_result_remains_pending() -> None:
    checkpointer = FakeCheckpointer(messages=[], pending_run_id="run-original")

    recovered = await resolve_foreground_checkpoint(
        _task(),
        redis=object(),
        redis_key_prefix="test",
        checkpointer=checkpointer,
        load_run_meta=AsyncMock(return_value=None),
    )

    assert recovered == "pending"
    checkpointer.mark_run_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_foreground_is_fenced_before_background_handoff() -> None:
    checkpointer = FakeCheckpointer(messages=[], pending_run_id=None)

    recovered = await resolve_foreground_checkpoint(
        _task(),
        redis=object(),
        redis_key_prefix="test",
        checkpointer=checkpointer,
        load_run_meta=AsyncMock(return_value=None),
    )

    assert recovered == "not_delivered"
    checkpointer.mark_run_complete.assert_awaited_once_with("conv-test", "run-original")
