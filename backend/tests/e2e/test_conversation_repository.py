"""E2E tests for ConversationRepository (direct repo-level access).

The HTTP-layer cases live in `test_conversations.py`; this file exercises
the repository signature directly — in particular the new ``topic_id``
kwarg on ``create``, which downstream callers (schedule + trigger
dispatch) rely on to place new conversations under a topic.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from cubeplex.models import User
from cubeplex.models.topic import Topic, TopicParticipant
from cubeplex.repositories import ConversationRepository
from tests.e2e.conftest import (
    DEFAULT_ORG_ID,
    DEFAULT_TEST_EMAIL,
    DEFAULT_WS_ID,
    _ensure_default_user_and_membership,
)

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def _default_user_id() -> str:
    """Resolve the seeded default user's id for repo-scoped tests."""
    await _ensure_default_user_and_membership()
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            user = (
                await session.execute(select(User).where(User.email == DEFAULT_TEST_EMAIL))
            ).scalar_one()
            return user.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_with_topic_id(_default_user_id: str) -> None:
    """``ConversationRepository.create`` accepts a topic_id and persists it.

    Bug this catches: scheduled-task / trigger dispatch paths that target
    a topic would silently fall back to ``topic_id IS NULL`` (personal
    conversation) if the kwarg were dropped — which would also break
    visibility for everyone except the run owner.
    """
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            topic = Topic(
                org_id=DEFAULT_ORG_ID,
                workspace_id=DEFAULT_WS_ID,
                creator_user_id=_default_user_id,
                title="conv-repo-topic-test",
            )
            session.add(topic)
            await session.flush()
            session.add(
                TopicParticipant(
                    topic_id=topic.id,
                    user_id=_default_user_id,
                    role="owner",
                )
            )
            await session.commit()

            repo = ConversationRepository(
                session,
                org_id=DEFAULT_ORG_ID,
                workspace_id=DEFAULT_WS_ID,
                user_id=_default_user_id,
            )
            conv = await repo.create(title="topic-conv", topic_id=topic.id)
            try:
                assert conv.topic_id == topic.id
                assert conv.creator_user_id == _default_user_id
                assert conv.workspace_id == DEFAULT_WS_ID
            finally:
                await session.delete(conv)
                await session.execute(
                    TopicParticipant.__table__.delete().where(TopicParticipant.topic_id == topic.id)
                )
                await session.delete(topic)
                await session.commit()
    finally:
        await engine.dispose()
