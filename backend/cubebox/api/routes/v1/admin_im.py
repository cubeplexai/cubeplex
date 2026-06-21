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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.routes.v1._im_runtime import build_im_list_out
from cubebox.api.schemas.im_connector import IMAccountListOut, IMAccountOut, ImRuntimeStatus
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
    cfg = account.config or {}
    return IMAccountOut(
        id=account.id,
        platform=account.platform,
        external_account_id=account.external_account_id,
        workspace_id=account.workspace_id,
        acting_user_id=account.acting_user_id,
        delivery_mode=account.delivery_mode,
        enabled=account.enabled,
        runtime=ImRuntimeStatus.unknown(),
        bot_app_name=cfg.get("bot_app_name") or None,
        bot_avatar_url=cfg.get("bot_avatar_url") or None,
    )


@router.get("/accounts", response_model=IMAccountListOut)
async def list_org_accounts(
    request: Request,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountListOut:
    svc = _service(session, backend, ctx)
    accounts = await svc.list_for_org()
    long_conns = getattr(request.app.state, "im_long_connections", None) or {}
    gateways = getattr(request.app.state, "im_gateways", None) or {}
    return await build_im_list_out(
        svc=svc, session=session, long_conns=long_conns, gateways=gateways, accounts=accounts
    )


async def _disconnect_account(request: Request, account_id: str) -> None:
    """Tear down a live connection (long-connection or gateway) if one exists.

    Disabling/deleting an account must stop the connection in process
    state; otherwise the captured account object continues feeding events
    into ``ingest_inbound_event`` and the bot keeps responding until the
    next API restart.
    """
    long_conns = getattr(request.app.state, "im_long_connections", None) or {}
    lc = long_conns.pop(account_id, None)
    if lc is not None:
        try:
            await lc.disconnect()
        except Exception:
            logger.opt(exception=True).warning(
                "[IM admin] failed to disconnect long-connection for {}",
                account_id,
            )
    gateways = getattr(request.app.state, "im_gateways", None) or {}
    gw = gateways.pop(account_id, None)
    if gw is not None:
        try:
            await gw.stop()
        except Exception:
            logger.opt(exception=True).warning(
                "[IM admin] failed to stop gateway for {}",
                account_id,
            )


@router.post("/accounts/{account_id}/disable", response_model=IMAccountOut)
async def disable_account(
    account_id: str,
    request: Request,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    account = await svc.set_enabled(account_id=account_id, enabled=False)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    await _disconnect_account(request, account_id)
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
    # NOTE: Re-binding a long-connection client requires the full lifespan
    # context (secret cache, ingest callable, client cache) that lives only
    # in ``_start_im_runtime``. v1 path for re-enable: restart the API. This
    # is documented in the setup guide. Webhook accounts pick up immediately
    # because the ingress route reloads the enabled flag per request.
    return _to_out(account)
