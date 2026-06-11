"""Enqueue an embedding job for every conversation in every workspace.

Resumable via search_backfill_progress. Idempotent — re-running picks up
where it left off, and a conversation already chunked just re-runs the
worker which replaces its chunks (one-shot).

Usage:
    cd backend
    uv run python scripts/dev/backfill_search_index.py --rate 5
"""

import argparse
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.db.engine import async_session_maker
from cubebox.models.conversation import Conversation
from cubebox.models.search_backfill_progress import SearchBackfillProgress
from cubebox.models.workspace import Workspace
from cubebox.repositories.embedding_job import EmbeddingJobRepository

logger = logging.getLogger("backfill")


async def _workspaces(session: AsyncSession) -> list[Workspace]:
    result = await session.execute(select(Workspace))
    return list(result.scalars().all())


async def _conversations_for_ws(
    session: AsyncSession, ws: Workspace, after: str | None
) -> list[Conversation]:
    stmt = select(Conversation).where(
        Conversation.workspace_id == ws.id,  # type: ignore[arg-type]
        Conversation.deleted_at.is_(None),  # type: ignore[union-attr]
    )
    if after:
        stmt = stmt.where(Conversation.id > after)  # type: ignore[arg-type]
    stmt = stmt.order_by(Conversation.id).limit(1000)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _progress(session: AsyncSession, ws: Workspace) -> SearchBackfillProgress:
    stmt = select(SearchBackfillProgress).where(
        SearchBackfillProgress.workspace_id == ws.id  # type: ignore[arg-type]
    )
    result = await session.execute(stmt)
    p = result.scalar_one_or_none()
    if p is None:
        p = SearchBackfillProgress(org_id=ws.org_id, workspace_id=ws.id)
        session.add(p)
        await session.commit()
        await session.refresh(p)
    return p


async def main(rate: float) -> None:
    delay = 1.0 / max(0.1, rate)
    async with async_session_maker() as session:
        wss = await _workspaces(session)
    for ws in wss:
        async with async_session_maker() as session:
            p = await _progress(session, ws)
            if p.done:
                logger.info("ws=%s already done; skipping", ws.id)
                continue
            after = p.last_conversation_id
        while True:
            async with async_session_maker() as session:
                convs = await _conversations_for_ws(session, ws, after)
            if not convs:
                async with async_session_maker() as session:
                    p = await _progress(session, ws)
                    p.done = True
                    session.add(p)
                    await session.commit()
                break
            for c in convs:
                async with async_session_maker() as session:
                    repo = EmbeddingJobRepository(
                        session,
                        org_id=c.org_id,
                        workspace_id=c.workspace_id,
                        user_id=c.creator_user_id,
                    )
                    await repo.enqueue(conversation_id=c.id)
                async with async_session_maker() as session:
                    p = await _progress(session, ws)
                    p.last_conversation_id = c.id
                    p.enqueued_count += 1
                    session.add(p)
                    await session.commit()
                logger.info("enqueued conv=%s ws=%s", c.id, ws.id)
                await asyncio.sleep(delay)
            after = convs[-1].id


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--rate", type=float, default=5.0, help="enqueues / sec")
    args = p.parse_args()
    asyncio.run(main(args.rate))
