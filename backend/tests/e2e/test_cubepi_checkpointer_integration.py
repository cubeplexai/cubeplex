"""Integration test: cubeplex uses cubepi.PostgresCheckpointer against
the alembic-created schema in the test database.

Requires alembic upgrade head to have run on the test DB. Fresh dev
worktree should have this from M0.3.
"""

import pytest
from cubepi.providers.base import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from cubeplex.agents.checkpointer import _build_dsn, init_checkpointer
from cubeplex.streams.run_manager import _repair_dangling_tool_calls


async def _delete_thread(thread_id: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(_build_dsn())
    try:
        await conn.execute("DELETE FROM cubepi_threads WHERE thread_id = $1", thread_id)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_repair_dangling_tool_calls_backfills_and_is_idempotent() -> None:
    """A thread left with an orphan tool_call (cancel mid-tool) is repaired
    with a synthetic tool_result, and repairing again adds nothing."""
    thread_id = "t-repair-dangling"
    async with init_checkpointer() as cp:
        await cp.append(
            thread_id,
            [
                UserMessage(content=[TextContent(text="run it")]),
                AssistantMessage(
                    content=[ToolCall(id="tc-orphan", name="execute", arguments={})],
                    stop_reason="tool_use",
                ),
            ],
        )

    try:
        await _repair_dangling_tool_calls(thread_id)
        async with init_checkpointer() as cp:
            data = await cp.load(thread_id)
        assert data is not None
        roles = [m.role for m in data.messages]
        assert roles == ["user", "assistant", "tool_result"]
        result = data.messages[2]
        assert isinstance(result, ToolResultMessage)
        assert result.tool_call_id == "tc-orphan"
        assert result.is_error is True

        # Idempotent: a second pass finds no orphan and appends nothing.
        await _repair_dangling_tool_calls(thread_id)
        async with init_checkpointer() as cp:
            data2 = await cp.load(thread_id)
        assert data2 is not None
        assert len(data2.messages) == 3
    finally:
        await _delete_thread(thread_id)


@pytest.mark.asyncio
async def test_cubepi_checkpointer_round_trip_against_real_schema() -> None:
    """Connecting cubepi.PostgresCheckpointer to the cubeplex dev DB
    must succeed (schema version check passes) and round-trip messages."""
    async with init_checkpointer() as cp:
        msg = UserMessage(
            content=[TextContent(text="hello m0-integration")],
            metadata={"test": True},
        )
        await cp.append("t-m0-integration", [msg])
        data = await cp.load("t-m0-integration")
        assert data is not None
        assert len(data.messages) == 1
        assert data.messages[0].metadata == {"test": True}
        # Cleanup: delete the test thread row (cascades to messages)
        import asyncpg

        conn = await asyncpg.connect(_build_dsn())
        try:
            await conn.execute("DELETE FROM cubepi_threads WHERE thread_id = 't-m0-integration'")
        finally:
            await conn.close()
