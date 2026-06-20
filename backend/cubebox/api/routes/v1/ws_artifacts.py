"""Workspace-level artifacts API routes (list + delete).

Scope-isolated from the conversation-scoped handler in ``artifacts.py``:
this serves the workspace "artifact library" audience. Reuse lives in the
repository layer, not here.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db import get_session
from cubebox.objectstore import get_objectstore_client
from cubebox.repositories import ArtifactRepository, ConversationRepository

router = APIRouter(prefix="/ws/{workspace_id}/artifacts", tags=["ws-artifacts"])


@router.get("")
async def list_workspace_artifacts(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    type: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    """List the caller's accessible artifacts in the workspace."""
    conv_repo = ConversationRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id, user_id=ctx.user.id
    )
    art_repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifacts, total = await art_repo.list_by_workspace(
        accessible_conv_subq=conv_repo.accessible_id_subquery(),
        artifact_type=type,
        name_query=q,
        limit=limit,
        offset=offset,
    )
    return {"artifacts": [a.to_dict() for a in artifacts], "total": total}


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace_artifact(
    artifact_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> Response:
    """Delete an artifact (DB rows + object-store files) if the caller may access it."""
    art_repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifact = await art_repo.get_by_id(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

    conv_repo = ConversationRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id, user_id=ctx.user.id
    )
    if (await conv_repo.get_by_id(artifact.conversation_id)) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

    conversation_id = artifact.conversation_id
    await art_repo.delete_with_versions(artifact)

    prefix = f"artifacts/{conversation_id}/{artifact_id}/"
    try:
        store = get_objectstore_client()
        for key in await store.list_objects(prefix):
            await store.delete_file(key)
    except Exception as e:  # storage cleanup is best-effort; rows are already gone
        logger.error("Artifact {} storage cleanup failed: {}", artifact_id, e)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
