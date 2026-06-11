"""Org-admin scope IM connector governance routes (Task 15).

Separate handler from ``ws_im`` — workspace and admin must not share a
route per the plan's scope-isolated-pages rule. Shared logic lives in
``IMConnectorService``.

Auth uses ``get_admin_request_context`` (backed by ``require_org_admin``),
NOT ``require_admin``: the admin routes have no ``{workspace_id}`` path
segment so the workspace-scoped role dependency cannot resolve.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.im_connector import IMAccountListOut, IMAccountOut
from cubebox.auth.context import RequestContext
from cubebox.credentials.dependencies import (
    build_credential_service,
    get_encryption_backend,
)
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db.session import get_session
from cubebox.mcp.dependencies import get_admin_request_context
from cubebox.models.im_connector import IMConnectorAccount
from cubebox.services.im_connector import IMConnectorService

router = APIRouter(prefix="/admin/im", tags=["admin-im"])


def _service(
    session: AsyncSession,
    backend: EncryptionBackend,
    ctx: RequestContext,
) -> IMConnectorService:
    creds = build_credential_service(
        session,
        backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id if ctx.user is not None else None,
    )
    return IMConnectorService(session, creds, org_id=ctx.org_id)


def _to_out(account: IMConnectorAccount) -> IMAccountOut:
    return IMAccountOut(
        id=account.id,
        platform=account.platform,
        external_account_id=account.external_account_id,
        workspace_id=account.workspace_id,
        acting_user_id=account.acting_user_id,
        delivery_mode=account.delivery_mode,
        enabled=account.enabled,
    )


@router.get("/accounts", response_model=IMAccountListOut)
async def list_org_accounts(
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountListOut:
    svc = _service(session, backend, ctx)
    return IMAccountListOut(accounts=[_to_out(a) for a in await svc.list_for_org()])


@router.post("/accounts/{account_id}/disable", response_model=IMAccountOut)
async def disable_account(
    account_id: str,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    account = await svc.set_enabled(account_id=account_id, enabled=False)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return _to_out(account)


@router.post("/accounts/{account_id}/enable", response_model=IMAccountOut)
async def enable_account(
    account_id: str,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    account = await svc.set_enabled(account_id=account_id, enabled=True)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return _to_out(account)
