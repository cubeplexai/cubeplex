"""Workspace conversation sandbox-command list and Kill."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.db import get_session
from cubeplex.models.sandbox_command import SandboxCommandStatus
from cubeplex.repositories import ConversationRepository
from cubeplex.repositories.sandbox_command import SandboxCommandRepository
from cubeplex.sandbox.command_coordinator import kill_command, sandbox_from_row
from cubeplex.utils.time import utc_isoformat

router = APIRouter(
    prefix="/ws/{workspace_id}/conversations/{conversation_id}/sandbox-commands",
    tags=["sandbox-commands"],
)


class SandboxCommandOut(BaseModel):
    id: str
    description: str
    status: str
    started_at: str
    kind: str
    lifetime: str


async def _require_conversation(
    session: AsyncSession, ctx: RequestContext, conversation_id: str
) -> None:
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    if (await repo.get_by_id(conversation_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )


@router.get("")
async def list_sandbox_commands(
    workspace_id: str,
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> list[SandboxCommandOut]:
    del workspace_id
    await _require_conversation(session, ctx, conversation_id)
    repo = SandboxCommandRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    rows = await repo.list_inflight_for_conversation(conversation_id)
    return [
        SandboxCommandOut(
            id=row.id,
            description=row.description or row.command[:80],
            status=row.status,
            started_at=utc_isoformat(row.created_at),
            kind=row.kind,
            lifetime=row.lifetime,
        )
        for row in rows
    ]


@router.post("/{command_id}/kill")
async def kill_sandbox_command(
    workspace_id: str,
    conversation_id: str,
    command_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    del workspace_id
    await _require_conversation(session, ctx, conversation_id)
    repo = SandboxCommandRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    row = await repo.get(command_id)
    if row is None or row.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"command not found: {command_id}",
        )

    async def _get_sandbox(_row: object) -> Any:
        del _row
        return await sandbox_from_row(row, session)

    if row.status in (SandboxCommandStatus.starting.value, SandboxCommandStatus.running.value):
        killed = await kill_command(session, row, get_sandbox=_get_sandbox)
        if not killed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"command is still running: {command_id}",
            )
        return {"id": command_id, "status": "killed"}
    return {"id": command_id, "status": row.status}
