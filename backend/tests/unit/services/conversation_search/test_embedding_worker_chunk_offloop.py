"""EmbeddingWorker must not run CPU chunking on the asyncio event loop."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cubepi.providers.base import TextContent, UserMessage

from cubeplex.services.conversation_search.chunker import Chunk
from cubeplex.services.conversation_search.worker import (
    EmbeddingWorker,
    build_chunks_for_messages,
)


def test_build_chunks_for_messages_respects_seq_window() -> None:
    messages = [
        UserMessage(content=[TextContent(text="one")]),
        UserMessage(content=[TextContent(text="two")]),
        UserMessage(content=[TextContent(text="three")]),
    ]
    chunks = build_chunks_for_messages(
        messages, seq_lo=2, seq_hi=3, target_tokens=600, overlap_tokens=100
    )
    assert len(chunks) == 1
    assert chunks[0].seq_lo == 2
    assert chunks[0].seq_hi == 3
    assert "two" in chunks[0].text
    assert "three" in chunks[0].text
    assert "one" not in chunks[0].text


@pytest.mark.asyncio
async def test_process_chunks_via_to_thread() -> None:
    worker = EmbeddingWorker(provider=None)
    job = SimpleNamespace(
        conversation_id="conv-1",
        seq_lo=1,
        seq_hi=10,
        org_id="org-1",
        workspace_id="ws-1",
        creator_user_id="usr-1",
        id="job-1",
        attempts=0,
    )
    fake_data = SimpleNamespace(messages=[UserMessage(content=[TextContent(text="hi")])])
    fake_chunks = [Chunk(chunk_seq=0, seq_lo=1, seq_hi=1, text="[user] hi")]

    cp = MagicMock()
    cp.load = AsyncMock(return_value=fake_data)
    cp_cm = MagicMock()
    cp_cm.__aenter__ = AsyncMock(return_value=cp)
    cp_cm.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=None)

    repo = MagicMock()
    repo.replace_for_conversation = AsyncMock()

    with (
        patch(
            "cubeplex.services.conversation_search.worker.shared_checkpointer",
            return_value=cp_cm,
        ),
        patch(
            "cubeplex.services.conversation_search.worker.async_session_maker",
            return_value=session_cm,
        ),
        patch(
            "cubeplex.services.conversation_search.worker.ConversationChunkRepository",
            return_value=repo,
        ),
        patch(
            "cubeplex.services.conversation_search.worker.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=fake_chunks,
        ) as to_thread,
    ):
        await worker._process(job)  # type: ignore[arg-type]

    to_thread.assert_awaited_once()
    assert to_thread.await_args is not None
    assert to_thread.await_args.args[0] is build_chunks_for_messages
    repo.replace_for_conversation.assert_awaited_once()
