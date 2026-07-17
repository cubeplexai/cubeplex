"""Public ingest endpoint — HMAC-authenticated, no require_member."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.cache import RedisHandle, redis_dep
from cubeplex.credentials.dependencies import get_encryption_backend
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.db.session import get_session
from cubeplex.triggers.ingest import handle_ingest

router = APIRouter(prefix="/ws/{workspace_id}/triggers", tags=["trigger-ingest"])


@router.post("/{trigger_id}/ingest")
async def ingest_webhook(
    workspace_id: str,
    trigger_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    rh: Annotated[RedisHandle, Depends(redis_dep)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> JSONResponse:
    return await handle_ingest(
        request=request,
        workspace_id=workspace_id,
        trigger_id=trigger_id,
        session=session,
        rh=rh,
        backend=backend,
    )
