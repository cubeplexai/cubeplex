"""Read-only artifact-library agent capability."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import AgentCapability, AgentOperation
from cubebox.repositories import ArtifactRepository, ConversationRepository


class ListInput(BaseModel):
    n: int = Field(default=10, ge=1, le=50)
    q: str | None = Field(default=None, max_length=255)
    artifact_type: str | None = Field(default=None, max_length=50)
    offset: int = Field(default=0, ge=0)


async def _list(ctx: ScopeContext, session: AsyncSession, inp: ListInput) -> dict[str, Any]:
    conversations = ConversationRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id, user_id=ctx.user_id
    )
    artifacts = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    items, total = await artifacts.list_by_workspace(
        accessible_conv_subq=conversations.accessible_id_subquery(),
        artifact_type=inp.artifact_type,
        name_query=inp.q,
        limit=inp.n,
        offset=inp.offset,
    )
    return {"artifacts": [artifact.to_dict() for artifact in items], "total": total}


ARTIFACTS_CAPABILITY = AgentCapability(
    name="artifacts",
    description="List artifacts from conversations you can access.",
    operations=[
        AgentOperation(
            name="list",
            description="List accessible artifacts, optionally filtered by name or type.",
            input_model=ListInput,
            handler=_list,
            mutates=False,
        )
    ],
)
