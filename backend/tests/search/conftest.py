"""Fixtures for tests/search/ — DB-backed integration helpers."""

import secrets

import pytest_asyncio
from cubepi.providers.base import AssistantMessage, TextContent, UserMessage

from cubebox.agents.checkpointer import init_checkpointer
from cubebox.db.engine import async_session_maker
from cubebox.models.conversation import Conversation
from cubebox.models.organization import Organization
from cubebox.models.user import User
from cubebox.models.workspace import Workspace


@pytest_asyncio.fixture
async def test_user_ctx() -> tuple[str, str, str]:
    """Create a minimal org / workspace / user trio and return their IDs.

    We bypass the fastapi_users registration flow used by tests/e2e/ — search
    tests don't authenticate, they just need scope IDs that satisfy FK
    constraints on Conversation / ConversationChunk / EmbeddingJob. The slug
    and email are randomized so concurrent / repeated runs don't collide.
    """
    suffix = secrets.token_hex(6)
    async with async_session_maker() as session:
        org = Organization(name=f"search-test-{suffix}", slug=f"search-test-{suffix}")
        session.add(org)
        await session.commit()
        await session.refresh(org)
        ws = Workspace(org_id=org.id, name="search-test-ws")
        session.add(ws)
        await session.commit()
        await session.refresh(ws)
        user = User(
            email=f"search-test-{suffix}@example.com",
            hashed_password="x",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return org.id, ws.id, user.id


@pytest_asyncio.fixture
async def seeded_conversation(
    test_user_ctx: tuple[str, str, str],
) -> tuple[str, str, str, str]:
    """Create a conversation and seed three small cubepi messages."""
    org_id, ws_id, user_id = test_user_ctx
    async with async_session_maker() as session:
        c = Conversation(
            org_id=org_id,
            workspace_id=ws_id,
            creator_user_id=user_id,
            title="seed",
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        conv_id = c.id
    async with init_checkpointer() as cp:
        await cp.append(
            conv_id,
            [
                UserMessage(content=[TextContent(text="hello docling")], timestamp=1.0),
                AssistantMessage(content=[TextContent(text="hi there")], timestamp=2.0),
                UserMessage(content=[TextContent(text="文档解析问题")], timestamp=3.0),
            ],
        )
    return org_id, ws_id, user_id, conv_id


@pytest_asyncio.fixture
async def seed_conversations_with_content(
    test_user_ctx: tuple[str, str, str],
) -> tuple[str, str, str, list[tuple[str, str]]]:
    """Seed three conversations: English keyword, Chinese keyword, unrelated.

    Returns ``(org_id, workspace_id, user_id, [(conv_id, gist), ...])`` so
    callers can drive embedding + assert which conversation they expect to
    find for each search query.
    """
    org_id, ws_id, user_id = test_user_ctx
    seeds: list[tuple[str, list[TextContent], str]] = [
        (
            "docling-en",
            [TextContent(text="docling is a PDF parser for agent pipelines")],
            "english docling",
        ),
        (
            "docling-zh",
            [TextContent(text="docling 是一款用于智能体的文档解析工具")],
            "chinese 文档解析",
        ),
        (
            "unrelated",
            [TextContent(text="weather is sunny today, no parsing here")],
            "unrelated",
        ),
    ]
    out: list[tuple[str, str]] = []
    for title, user_content, gist in seeds:
        async with async_session_maker() as session:
            c = Conversation(
                org_id=org_id,
                workspace_id=ws_id,
                creator_user_id=user_id,
                title=title,
            )
            session.add(c)
            await session.commit()
            await session.refresh(c)
            conv_id = c.id
        async with init_checkpointer() as cp:
            await cp.append(
                conv_id,
                [
                    UserMessage(content=user_content, timestamp=1.0),
                    AssistantMessage(content=[TextContent(text="ack")], timestamp=2.0),
                ],
            )
        out.append((conv_id, gist))
    return org_id, ws_id, user_id, out
