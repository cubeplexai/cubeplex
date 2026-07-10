"""E2E contracts for read-only conversation-history and artifact action tools."""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from cubepi.providers.base import AssistantMessage, TextContent, ToolResultMessage, UserMessage
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.agents.actions.builder import build_capability_tools
from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import AgentCapability
from cubebox.agents.checkpointer import init_checkpointer
from cubebox.db.engine import _build_database_url
from cubebox.models.membership import Role
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID


@dataclass(frozen=True)
class Seed:
    member_context: ScopeContext
    stranger_context: ScopeContext
    visible_conversation_id: str
    private_conversation_id: str
    visible_artifact_id: str
    hidden_artifact_id: str
    tool_call_id: str


@pytest_asyncio.fixture
async def seed(client: Any) -> AsyncIterator[Seed]:
    suffix = secrets.token_hex(6)
    member_id = str(client.get("/api/v1/auth/me").json()["id"])
    stranger_id = f"usr-ha-{suffix}"
    visible_conversation_id = f"conv-ha-{suffix}"
    private_conversation_id = f"conv-hp-{suffix}"
    visible_artifact_id = f"art-ha-{suffix}"
    hidden_artifact_id = f"art-hp-{suffix}"
    tool_call_id = f"tool-ha-{suffix}"
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await session.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, is_active, is_superuser, "
                    "is_verified, created_at, language) VALUES (:id, :email, 'x', true, false, "
                    "false, NOW(), 'en')"
                ),
                {"id": stranger_id, "email": f"{stranger_id}@example.com"},
            )
            for conversation_id, user_id in (
                (visible_conversation_id, member_id),
                (private_conversation_id, stranger_id),
            ):
                await session.execute(
                    text(
                        "INSERT INTO conversations (id, org_id, workspace_id, creator_user_id, "
                        "title, has_messages, is_group_chat, reasoning, attributes, created_at, "
                        "updated_at) VALUES (:id, :org_id, :workspace_id, :user_id, :title, true, "
                        "false, '{}'::jsonb, '{}'::jsonb, NOW(), NOW())"
                    ),
                    {
                        "id": conversation_id,
                        "org_id": DEFAULT_ORG_ID,
                        "workspace_id": DEFAULT_WS_ID,
                        "user_id": user_id,
                        "title": "history action seed",
                    },
                )
            for artifact_id, conversation_id in (
                (visible_artifact_id, visible_conversation_id),
                (hidden_artifact_id, private_conversation_id),
            ):
                await session.execute(
                    text(
                        "INSERT INTO artifacts (id, org_id, workspace_id, conversation_id, name, "
                        "artifact_type, path, version, created_at, updated_at) VALUES "
                        "(:id, :org_id, :workspace_id, :conversation_id, :name, 'report', '/tmp/a', "
                        "1, NOW(), NOW())"
                    ),
                    {
                        "id": artifact_id,
                        "org_id": DEFAULT_ORG_ID,
                        "workspace_id": DEFAULT_WS_ID,
                        "conversation_id": conversation_id,
                        "name": artifact_id,
                    },
                )
            await session.commit()
        async with init_checkpointer() as checkpointer:
            await checkpointer.append(
                visible_conversation_id,
                [
                    UserMessage(content=[TextContent(text="one")], timestamp=1.0),
                    AssistantMessage(content=[TextContent(text="two")], timestamp=2.0),
                    UserMessage(content=[TextContent(text="three")], timestamp=3.0),
                    AssistantMessage(content=[TextContent(text="four")], timestamp=4.0),
                    UserMessage(content=[TextContent(text="five")], timestamp=5.0),
                    AssistantMessage(content=[TextContent(text="six")], timestamp=6.0),
                    UserMessage(content=[TextContent(text="seven")], timestamp=7.0),
                    AssistantMessage(content=[TextContent(text="eight")], timestamp=8.0),
                    UserMessage(content=[TextContent(text="nine")], timestamp=9.0),
                    AssistantMessage(content=[TextContent(text="ten")], timestamp=10.0),
                    ToolResultMessage(
                        tool_call_id=tool_call_id,
                        tool_name="lookup",
                        content=[TextContent(text="targeted result")],
                        timestamp=11.0,
                    ),
                ],
            )
        yield Seed(
            member_context=ScopeContext(
                org_id=DEFAULT_ORG_ID,
                workspace_id=DEFAULT_WS_ID,
                user_id=member_id,
                role=Role.MEMBER,
            ),
            stranger_context=ScopeContext(
                org_id=DEFAULT_ORG_ID,
                workspace_id=DEFAULT_WS_ID,
                user_id=stranger_id,
                role=Role.MEMBER,
            ),
            visible_conversation_id=visible_conversation_id,
            private_conversation_id=private_conversation_id,
            visible_artifact_id=visible_artifact_id,
            hidden_artifact_id=hidden_artifact_id,
            tool_call_id=tool_call_id,
        )
    finally:
        async with maker() as session:
            for conversation_id in (visible_conversation_id, private_conversation_id):
                await session.execute(
                    text("DELETE FROM conversation_chunks WHERE conversation_id = :id"),
                    {"id": conversation_id},
                )
                await session.execute(
                    text("DELETE FROM embedding_jobs WHERE conversation_id = :id"),
                    {"id": conversation_id},
                )
                await session.execute(
                    text("DELETE FROM cubepi_messages WHERE thread_id = :id"),
                    {"id": conversation_id},
                )
                await session.execute(
                    text("DELETE FROM cubepi_runs WHERE thread_id = :id"), {"id": conversation_id}
                )
                await session.execute(
                    text("DELETE FROM cubepi_threads WHERE thread_id = :id"),
                    {"id": conversation_id},
                )
            await session.execute(
                text("DELETE FROM artifacts WHERE id IN (:visible, :hidden)"),
                {"visible": visible_artifact_id, "hidden": hidden_artifact_id},
            )
            await session.execute(
                text("DELETE FROM conversations WHERE id IN (:visible, :private)"),
                {"visible": visible_conversation_id, "private": private_conversation_id},
            )
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": stranger_id})
            await session.commit()
        await engine.dispose()


async def _invoke(
    capability: AgentCapability, context: ScopeContext, operation: str, **kwargs: Any
) -> Any:
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def context_factory() -> AsyncIterator[tuple[ScopeContext, AsyncSession]]:
        async with maker() as session:
            yield context, session

    try:
        tool = next(
            tool
            for tool in build_capability_tools(capability, context_factory, allow_mutations=False)
            if tool.name == f"{capability.name}_{operation}"
        )
        result = await tool.execute("call-history", tool.parameters(**kwargs))
        return result
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_history_read_rejects_a_conversation_outside_the_caller_scope(seed: Seed) -> None:
    from cubebox.agents.actions.capabilities.conversation_history import (
        ConversationHistoryDeps,
        build_conversation_history_capability,
    )

    result = await _invoke(
        build_conversation_history_capability(ConversationHistoryDeps(None, None)),
        seed.member_context,
        "read",
        conversation_id=seed.private_conversation_id,
    )

    assert result.is_error is True
    assert "ActionNotFound" in result.content[0].text


@pytest.mark.asyncio
async def test_history_tools_return_formatted_visible_checkpoint_data(seed: Seed) -> None:
    from cubebox.agents.actions.capabilities.conversation_history import (
        ConversationHistoryDeps,
        build_conversation_history_capability,
    )

    capability = build_conversation_history_capability(ConversationHistoryDeps(None, None))
    read = await _invoke(
        capability, seed.member_context, "read", conversation_id=seed.visible_conversation_id, n=5
    )
    assert read.is_error is None
    read_payload = json.loads(read.content[0].text)
    assert len(read_payload["turns"]) == 5
    assert "content" not in json.dumps(read_payload["turns"])

    tool_result = await _invoke(
        capability,
        seed.member_context,
        "tool_result",
        conversation_id=seed.visible_conversation_id,
        tool_call_id=seed.tool_call_id,
        max_tokens=256,
    )
    assert tool_result.is_error is None
    assert json.loads(tool_result.content[0].text)["content"] == "targeted result"


@pytest.mark.asyncio
async def test_history_search_returns_only_visible_conversation_context_ids(seed: Seed) -> None:
    from cubebox.agents.actions.capabilities.conversation_history import (
        ConversationHistoryDeps,
        build_conversation_history_capability,
    )
    from cubebox.db.engine import async_session_maker
    from cubebox.repositories.embedding_job import EmbeddingJobRepository
    from cubebox.services.conversation_search.worker import EmbeddingWorker

    async with async_session_maker() as session:
        await EmbeddingJobRepository(
            session,
            org_id=seed.member_context.org_id,
            workspace_id=seed.member_context.workspace_id,
            user_id=seed.member_context.user_id,
        ).enqueue(conversation_id=seed.visible_conversation_id)
    await EmbeddingWorker(None)._claim_one()

    result = await _invoke(
        build_conversation_history_capability(ConversationHistoryDeps(None, None)),
        seed.member_context,
        "search",
        q="one",
    )

    assert result.is_error is None
    assert {item["conversation_id"] for item in json.loads(result.content[0].text)["results"]} == {
        seed.visible_conversation_id
    }


@pytest.mark.asyncio
async def test_artifact_list_excludes_inaccessible_conversation_artifacts(seed: Seed) -> None:
    from cubebox.agents.actions.capabilities.artifacts import ARTIFACTS_CAPABILITY, ListInput
    from cubebox.agents.actions.capabilities.conversation_history import ReadInput

    result = await _invoke(ARTIFACTS_CAPABILITY, seed.member_context, "list", n=10)
    payload = result.content[0].text

    assert seed.visible_artifact_id in payload
    assert seed.hidden_artifact_id not in payload

    with pytest.raises(ValidationError):
        ListInput(n=51)
    with pytest.raises(ValidationError):
        ReadInput(conversation_id=seed.visible_conversation_id, max_tokens=255)
