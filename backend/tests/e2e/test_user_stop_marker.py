"""A user Stop leaves a durable aborted marker on that run's transcript."""

from __future__ import annotations

from uuid import uuid4

import pytest
from cubeloop.providers.base import AssistantMessage, TextContent, ToolCall, UserMessage

from cubeplex.agents.checkpointer import _build_dsn, init_checkpointer
from cubeplex.agents.user_stop import USER_STOP_KIND
from cubeplex.streams.user_stop import mark_user_stopped_run


async def _delete_thread(thread_id: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(_build_dsn())
    try:
        await conn.execute("DELETE FROM cubepi_threads WHERE thread_id = $1", thread_id)
    finally:
        await conn.close()


def _thread_id() -> str:
    return f"t-user-stop-{uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_user_stop_marker_appends_aborted_after_tool_use_and_is_idempotent() -> None:
    thread_id = _thread_id()
    run_id = "run-stop"
    try:
        async with init_checkpointer() as cp:
            await cp.append(
                thread_id,
                [
                    UserMessage(content=[TextContent(text="run it")], run_id=run_id),
                    AssistantMessage(
                        content=[
                            TextContent(text="partial"),
                            ToolCall(id="tc-1", name="execute", arguments={}),
                        ],
                        stop_reason="tool_use",
                        run_id=run_id,
                    ),
                ],
            )

        assert await mark_user_stopped_run(thread_id, run_id) is True
        assert await mark_user_stopped_run(thread_id, run_id) is False

        async with init_checkpointer() as cp:
            data = await cp.load(thread_id)
        assert data is not None
        assistants = [m for m in data.messages if isinstance(m, AssistantMessage)]
        assert len(assistants) == 2
        marker = assistants[-1]
        assert marker.stop_reason == "aborted"
        assert marker.content == []
        assert marker.run_id == run_id
        assert marker.metadata.get("kind") == USER_STOP_KIND
        assert assistants[0].content[0] == TextContent(text="partial")
    finally:
        await _delete_thread(thread_id)


@pytest.mark.asyncio
async def test_user_stop_marker_skips_aborted_and_error() -> None:
    thread_id = _thread_id()
    try:
        async with init_checkpointer() as cp:
            await cp.append(
                thread_id,
                [
                    UserMessage(content=[TextContent(text="a")], run_id="run-aborted"),
                    AssistantMessage(
                        content=[TextContent(text="stopped already")],
                        stop_reason="aborted",
                        run_id="run-aborted",
                    ),
                    UserMessage(content=[TextContent(text="b")], run_id="run-error"),
                    AssistantMessage(
                        content=[TextContent(text="boom")],
                        stop_reason="error",
                        run_id="run-error",
                    ),
                ],
            )

        assert await mark_user_stopped_run(thread_id, "run-aborted") is False
        assert await mark_user_stopped_run(thread_id, "run-error") is False

        async with init_checkpointer() as cp:
            data = await cp.load(thread_id)
        assert data is not None
        assert len(data.messages) == 4
    finally:
        await _delete_thread(thread_id)


@pytest.mark.asyncio
async def test_user_stop_marker_appends_when_the_run_has_no_assistant() -> None:
    thread_id = _thread_id()
    run_id = "run-user-only"
    try:
        async with init_checkpointer() as cp:
            await cp.append(
                thread_id,
                [UserMessage(content=[TextContent(text="hello")], run_id=run_id)],
            )

        assert await mark_user_stopped_run(thread_id, run_id) is True

        async with init_checkpointer() as cp:
            data = await cp.load(thread_id)
        assert data is not None
        assert [m.role for m in data.messages] == ["user", "assistant"]
        marker = data.messages[1]
        assert isinstance(marker, AssistantMessage)
        assert marker.stop_reason == "aborted"
        assert marker.metadata.get("kind") == USER_STOP_KIND
    finally:
        await _delete_thread(thread_id)


@pytest.mark.asyncio
async def test_user_stop_marker_does_not_touch_a_run_that_never_started() -> None:
    thread_id = _thread_id()
    try:
        async with init_checkpointer() as cp:
            await cp.append(
                thread_id,
                [
                    UserMessage(content=[TextContent(text="old")], run_id="run-old"),
                    AssistantMessage(
                        content=[TextContent(text="done")],
                        stop_reason="stop",
                        run_id="run-old",
                    ),
                ],
            )

        assert await mark_user_stopped_run(thread_id, "run-new") is False

        async with init_checkpointer() as cp:
            data = await cp.load(thread_id)
        assert data is not None
        assert len(data.messages) == 2
        last = data.messages[-1]
        assert isinstance(last, AssistantMessage)
        assert last.stop_reason == "stop"
        assert last.run_id == "run-old"
    finally:
        await _delete_thread(thread_id)


@pytest.mark.asyncio
async def test_user_stop_marker_does_not_raise_when_the_run_is_already_complete() -> None:
    thread_id = _thread_id()
    run_id = "run-done"
    try:
        async with init_checkpointer() as cp:
            await cp.append(
                thread_id,
                [
                    UserMessage(content=[TextContent(text="go")], run_id=run_id),
                    AssistantMessage(
                        content=[TextContent(text="working")],
                        stop_reason="tool_use",
                        run_id=run_id,
                    ),
                ],
            )
            await cp.claim_run(thread_id, run_id)
            await cp.mark_run_complete(thread_id, run_id)

        assert await mark_user_stopped_run(thread_id, run_id) is False

        async with init_checkpointer() as cp:
            data = await cp.load(thread_id)
        assert data is not None
        assert len(data.messages) == 2
    finally:
        await _delete_thread(thread_id)
