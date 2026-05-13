"""Integration test: cubebox uses cubepi.PostgresCheckpointer against
the alembic-created schema in the test database.

Requires alembic upgrade head to have run on the test DB. Fresh dev
worktree should have this from M0.3.
"""

import pytest
from cubepi.providers.base import TextContent, UserMessage

from cubebox.agents.checkpointer_pi import _build_dsn, init_cubepi_checkpointer


@pytest.mark.asyncio
async def test_cubepi_checkpointer_round_trip_against_real_schema() -> None:
    """Connecting cubepi.PostgresCheckpointer to the cubebox dev DB
    must succeed (schema version check passes) and round-trip messages."""
    async with init_cubepi_checkpointer() as cp:
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
