"""Unified sandbox scope resolution across runtime, HTTP panels, and background tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.models.workspace import Workspace

WorkspaceSandboxMode = Literal["per_user", "workspace"]
WORKSPACE_SANDBOX_MODES: tuple[WorkspaceSandboxMode, ...] = ("per_user", "workspace")


@dataclass(frozen=True, slots=True)
class ResolvedSandboxTarget:
    """Authoritative sandbox scope + owner tuple for a workspace operation."""

    scope_type: Literal["workspace", "user", "conversation", "topic"]
    scope_id: str
    owner_user_id: str | None
    workspace_sandbox_mode: WorkspaceSandboxMode


def resolve_sandbox_target_pure(
    *,
    workspace_id: str,
    workspace_sandbox_mode: WorkspaceSandboxMode,
    caller_user_id: str,
    conversation_id: str | None = None,
    is_group_chat: bool = False,
    conversation_creator_user_id: str | None = None,
    topic_id: str | None = None,
    topic_sandbox_mode: str | None = None,
    topic_creator_user_id: str | None = None,
) -> ResolvedSandboxTarget:
    """Pure scope resolution from preloaded workspace/conversation/topic attributes."""
    if workspace_sandbox_mode == "workspace":
        return ResolvedSandboxTarget(
            scope_type="workspace",
            scope_id=workspace_id,
            owner_user_id=None,
            workspace_sandbox_mode="workspace",
        )

    if topic_id is None:
        if is_group_chat and conversation_id is not None:
            owner = conversation_creator_user_id or caller_user_id
            return ResolvedSandboxTarget(
                scope_type="conversation",
                scope_id=conversation_id,
                owner_user_id=owner,
                workspace_sandbox_mode="per_user",
            )
        return ResolvedSandboxTarget(
            scope_type="user",
            scope_id=caller_user_id,
            owner_user_id=caller_user_id,
            workspace_sandbox_mode="per_user",
        )

    effective_mode = topic_sandbox_mode or "creator"
    topic_owner = topic_creator_user_id or caller_user_id
    if effective_mode == "dedicated":
        return ResolvedSandboxTarget(
            scope_type="topic",
            scope_id=topic_id,
            owner_user_id=topic_owner,
            workspace_sandbox_mode="per_user",
        )
    return ResolvedSandboxTarget(
        scope_type="user",
        scope_id=topic_owner,
        owner_user_id=topic_owner,
        workspace_sandbox_mode="per_user",
    )


async def get_workspace_sandbox_mode(
    session: AsyncSession | None, workspace_id: str
) -> WorkspaceSandboxMode:
    """Load ``Workspace.sandbox_mode``; returns ``'per_user'`` when ``session is None``."""
    if session is None:
        return "per_user"
    raw = await session.scalar(
        select(col(Workspace.sandbox_mode)).where(col(Workspace.id) == workspace_id)
    )
    if raw is None:
        raise LookupError(f"Workspace {workspace_id} not found")
    if raw == "workspace":
        return "workspace"
    return "per_user"
