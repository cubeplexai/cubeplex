"""Memory REST endpoints. All routes are workspace-scoped."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db import get_session
from cubebox.models.memory import MemoryScope, MemoryStatus, MemoryType
from cubebox.repositories.memory import MemoryRepository
from cubebox.services.memory import (
    CreateMemoryInput,
    MemoryPermissionError,
    MemoryService,
)
from cubebox.services.memory_screen import MemoryScreenError

router = APIRouter(prefix="/ws/{workspace_id}/memory", tags=["memory"])


class MemoryCreateBody(BaseModel):
    scope: MemoryScope
    type: MemoryType
    content: str = Field(min_length=1, max_length=5000)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class MemoryUpdateBody(BaseModel):
    content: str | None = Field(default=None, max_length=5000)
    type: MemoryType | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: MemoryStatus | None = None


def _service(ctx: RequestContext, session: AsyncSession) -> MemoryService:
    repo = MemoryRepository(
        session, user_id=ctx.user.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    return MemoryService(
        repo, user_id=ctx.user.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )


@router.get("")
async def list_memory(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    scope: MemoryScope | None = Query(default=None),
    type: MemoryType | None = Query(default=None),
    status: MemoryStatus = Query(default=MemoryStatus.ACTIVE),
    q: str | None = Query(default=None),
    source_conversation_id: str | None = Query(default=None),
) -> dict[str, object]:
    svc = _service(ctx, session)
    items = await svc.repo.list(
        scope=scope,
        type_=type,
        status=status,
        q=q,
        source_conversation_id=source_conversation_id,
    )
    return {"items": [i.to_dict() for i in items]}


@router.get("/count")
async def count_memory(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    scope: MemoryScope | None = Query(default=None),
    status: MemoryStatus = Query(default=MemoryStatus.ACTIVE),
    source_conversation_id: str | None = Query(default=None),
) -> dict[str, int]:
    """Count visible memories under the current workspace's scope rules.

    Used by the conversation chip — it only needs a number, not the rows.
    Honors the same scope visibility as list_memory (personal-of-current-user
    OR workspace-of-current-ws OR org-of-current-org).
    """
    svc = _service(ctx, session)
    count = await svc.repo.count(
        scope=scope,
        status=status,
        source_conversation_id=source_conversation_id,
    )
    return {"count": count}


@router.post("", status_code=201)
async def create_memory(
    body: MemoryCreateBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    svc = _service(ctx, session)
    try:
        item = await svc.create(
            CreateMemoryInput(
                scope=body.scope,
                type=body.type,
                content=body.content,
                confidence=body.confidence,
            )
        )
    except MemoryPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except MemoryScreenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.to_dict()


@router.patch("/{memory_id}")
async def update_memory(
    memory_id: str,
    body: MemoryUpdateBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    svc = _service(ctx, session)
    try:
        item = await svc.update(
            memory_id,
            content=body.content,
            type_=body.type,
            confidence=body.confidence,
            status=body.status,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="memory not found"
        ) from exc
    except MemoryScreenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.to_dict()


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    """Soft-delete: set status=archived. Hard delete is not exposed in v1."""
    svc = _service(ctx, session)
    try:
        await svc.archive(memory_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="memory not found"
        ) from exc
    return None
