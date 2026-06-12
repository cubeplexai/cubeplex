"""Workspace-scoped conversation search."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.conversation_search import (
    SearchResponseSchema,
    SearchResultSchema,
)
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db import get_session
from cubebox.search.service import ConversationSearchService

router = APIRouter(prefix="/ws/{workspace_id}/conversations", tags=["conversations"])


@router.get("/search", response_model=SearchResponseSchema)
async def search_conversations(
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> SearchResponseSchema:
    cleaned = q.strip()
    if not cleaned:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="empty query")
    # Provider is created once at lifespan startup (Task 19) and shared across
    # all requests; building one per request would re-init httpx connection
    # pools and re-parse config on every keystroke. provider may be None
    # when no embedding key is configured — the service degrades to the
    # lexical leg only.
    provider = getattr(raw_request.app.state, "embedding_provider", None)
    lexical_backend = getattr(raw_request.app.state, "lexical_backend", None)
    svc = ConversationSearchService(session, provider, lexical_backend=lexical_backend)
    resp = await svc.search(
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        creator_user_id=ctx.user.id,
        q=cleaned,
        limit=limit,
    )
    return SearchResponseSchema(
        results=[SearchResultSchema(**r.__dict__) for r in resp.results],
        lexical_count=resp.lexical_count,
        vector_count=resp.vector_count,
        fused_count=resp.fused_count,
    )
