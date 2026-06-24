"""E2E for ``load_history_window`` — the paginated reader behind the
conversation bootstrap and the messages-window endpoint.

The contract these tests pin:

- Default (no cursor) returns the newest ``limit`` messages, oldest-first.
- ``has_more`` is true iff at least one message exists below ``oldest_seq``.
- ``before_seq`` is exclusive — passing the previous slice's ``oldest_seq``
  yields the next older window with no overlap.
- Empty / non-existent thread returns no rows and ``has_more=False``.
"""

from __future__ import annotations

import asyncpg
import pytest
from cubepi.providers.base import TextContent, UserMessage
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.agents.checkpointer import _build_dsn, init_checkpointer
from cubebox.services.history_window import load_history_window

pytestmark = pytest.mark.e2e


async def _delete_thread(thread_id: str) -> None:
    conn = await asyncpg.connect(_build_dsn())
    try:
        await conn.execute("DELETE FROM cubepi_threads WHERE thread_id = $1", thread_id)
    finally:
        await conn.close()


async def _seed(thread_id: str, count: int) -> None:
    async with init_checkpointer() as cp:
        await cp.append(
            thread_id,
            [UserMessage(content=[TextContent(text=f"msg-{i}")]) for i in range(count)],
        )


@pytest.mark.asyncio
async def test_returns_tail_in_chronological_order(db_session: AsyncSession) -> None:
    """Without a cursor the loader returns the newest ``limit`` messages,
    flipped back to oldest-first so the frontend can render them in order."""
    thread_id = "t-hwin-tail"
    await _seed(thread_id, 10)
    try:
        window = await load_history_window(db_session, thread_id, limit=3)
        texts = [m["content"][0]["text"] for m in window.messages]
        assert texts == ["msg-7", "msg-8", "msg-9"]
        assert window.has_more is True
        assert window.oldest_seq is not None
    finally:
        await _delete_thread(thread_id)


@pytest.mark.asyncio
async def test_before_seq_cursor_yields_next_older_page(db_session: AsyncSession) -> None:
    """``before_seq`` is exclusive — the previous slice's ``oldest_seq`` cursor
    fetches the immediately preceding window without overlap."""
    thread_id = "t-hwin-cursor"
    await _seed(thread_id, 6)
    try:
        latest = await load_history_window(db_session, thread_id, limit=2)
        assert [m["content"][0]["text"] for m in latest.messages] == ["msg-4", "msg-5"]
        assert latest.has_more is True

        older = await load_history_window(
            db_session, thread_id, before_seq=latest.oldest_seq, limit=2
        )
        assert [m["content"][0]["text"] for m in older.messages] == ["msg-2", "msg-3"]
        assert older.has_more is True

        oldest = await load_history_window(
            db_session, thread_id, before_seq=older.oldest_seq, limit=2
        )
        assert [m["content"][0]["text"] for m in oldest.messages] == ["msg-0", "msg-1"]
        assert oldest.has_more is False
    finally:
        await _delete_thread(thread_id)


@pytest.mark.asyncio
async def test_has_more_is_false_when_limit_covers_history(
    db_session: AsyncSession,
) -> None:
    thread_id = "t-hwin-fits"
    await _seed(thread_id, 3)
    try:
        window = await load_history_window(db_session, thread_id, limit=10)
        assert len(window.messages) == 3
        assert window.has_more is False
    finally:
        await _delete_thread(thread_id)


@pytest.mark.asyncio
async def test_empty_thread_returns_empty_window(db_session: AsyncSession) -> None:
    window = await load_history_window(db_session, "t-hwin-nope", limit=50)
    assert window.messages == []
    assert window.oldest_seq is None
    assert window.has_more is False


@pytest.mark.asyncio
async def test_zero_limit_short_circuits(db_session: AsyncSession) -> None:
    thread_id = "t-hwin-zero"
    await _seed(thread_id, 2)
    try:
        window = await load_history_window(db_session, thread_id, limit=0)
        assert window.messages == []
        assert window.has_more is False
    finally:
        await _delete_thread(thread_id)
