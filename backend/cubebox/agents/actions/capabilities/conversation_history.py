"""Read-only, scope-aware tools for searching and inspecting conversations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import ActionNotFound, AgentCapability, AgentOperation
from cubebox.models import Conversation
from cubebox.repositories import ConversationRepository
from cubebox.services.conversation_search.embedding import EmbeddingProvider
from cubebox.services.conversation_search.history import format_history_turns, format_tool_result
from cubebox.services.conversation_search.lexical import LexicalSearchBackend
from cubebox.services.conversation_search.service import ConversationSearchService
from cubebox.services.history_window import load_history_window


@dataclass(frozen=True)
class ConversationHistoryDeps:
    provider: EmbeddingProvider | None
    lexical_backend: LexicalSearchBackend | None


class SearchInput(BaseModel):
    q: str = Field(min_length=1, max_length=255)
    n: int = Field(default=10, ge=1, le=20)


class ReadInput(BaseModel):
    conversation_id: str
    n: int = Field(default=5, ge=1, le=20)
    max_tokens: int = Field(default=4_000, ge=256, le=12_000)
    before_seq: int | None = Field(default=None, ge=1)


class ToolResultInput(BaseModel):
    conversation_id: str
    tool_call_id: str
    max_tokens: int = Field(default=4_000, ge=256, le=12_000)


async def _visible_conversation(
    ctx: ScopeContext, session: AsyncSession, conversation_id: str
) -> Conversation:
    repo = ConversationRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id, user_id=ctx.user_id
    )
    conversation = await repo.get_by_id(conversation_id)
    if conversation is None:
        raise ActionNotFound("conversation not found")
    return conversation


async def _checkpoint_messages(session: AsyncSession, conversation_id: str) -> list[dict[str, Any]]:
    return (await load_history_window(session, conversation_id, limit=200)).messages


def build_conversation_history_capability(deps: ConversationHistoryDeps) -> AgentCapability:
    """Build handlers that close over the app's search dependencies."""

    async def search(ctx: ScopeContext, session: AsyncSession, inp: SearchInput) -> dict[str, Any]:
        service = ConversationSearchService(
            session, deps.provider, lexical_backend=deps.lexical_backend
        )
        response = await service.search(
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
            creator_user_id=ctx.user_id,
            q=inp.q,
            limit=inp.n,
        )
        results: list[dict[str, Any]] = []
        for result in response.results:
            # The search service's index is creator-scoped. Re-check through
            # ConversationRepository so every emitted id follows the action
            # layer's canonical participant/archival visibility rules.
            try:
                await _visible_conversation(ctx, session, result.conversation_id)
            except ActionNotFound:
                continue
            results.append(
                {
                    "conversation_id": result.conversation_id,
                    "title": result.title,
                    "snippet": result.snippet,
                    "match_offsets": result.match_offsets,
                    "matched_message_seq": result.matched_message_seq,
                    "matched_at": result.matched_at,
                }
            )
        return {"results": results}

    async def read(ctx: ScopeContext, session: AsyncSession, inp: ReadInput) -> dict[str, Any]:
        await _visible_conversation(ctx, session, inp.conversation_id)
        page = format_history_turns(
            await _checkpoint_messages(session, inp.conversation_id),
            n=inp.n,
            max_tokens=inp.max_tokens,
            before_seq=inp.before_seq,
        )
        return {
            "turns": page.turns,
            "has_more": page.has_more,
            "next_before_seq": page.next_before_seq,
            "estimated_tokens": page.estimated_tokens,
            "truncated": page.truncated,
        }

    async def tool_result(
        ctx: ScopeContext, session: AsyncSession, inp: ToolResultInput
    ) -> dict[str, Any]:
        await _visible_conversation(ctx, session, inp.conversation_id)
        result = format_tool_result(
            await _checkpoint_messages(session, inp.conversation_id),
            tool_call_id=inp.tool_call_id,
            max_tokens=inp.max_tokens,
        )
        if result is None:
            raise ActionNotFound("tool result not found")
        return {
            "tool_call_id": result.tool_call_id,
            "tool_name": result.tool_name,
            "content": result.content,
            "is_error": result.is_error,
            "estimated_tokens": result.estimated_tokens,
            "truncated": result.truncated,
        }

    return AgentCapability(
        name="conversation_history",
        description="Search and read conversations you can access, including specific tool results.",
        operations=[
            AgentOperation(
                "search", "Search accessible conversation context.", SearchInput, search
            ),
            AgentOperation(
                "read",
                "Read formatted conversation turns without tool result bodies.",
                ReadInput,
                read,
            ),
            AgentOperation(
                "tool_result",
                "Read one full tool result from an accessible conversation.",
                ToolResultInput,
                tool_result,
            ),
        ],
    )
