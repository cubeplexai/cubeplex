"""Workspace-scope IM connector routes (Task 15)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.im_connector import (
    ConnectFeishuAccountIn,
    IMAccountListOut,
    IMAccountOut,
)
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.credentials.dependencies import (
    build_credential_service,
    get_encryption_backend,
)
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db.session import get_session
from cubebox.models.im_connector import IMConnectorAccount
from cubebox.models.membership import Role
from cubebox.repositories.membership import MembershipRepository
from cubebox.repositories.organization_membership import OrganizationMembershipRepository
from cubebox.services.im_connector import IMConnectorService

router = APIRouter(prefix="/ws/{workspace_id}/im", tags=["ws-im"])


def _service(
    session: AsyncSession,
    backend: EncryptionBackend,
    ctx: RequestContext,
) -> IMConnectorService:
    creds = build_credential_service(session, backend, org_id=ctx.org_id, actor_user_id=ctx.user.id)
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


@router.post("/accounts", status_code=status.HTTP_201_CREATED, response_model=IMAccountOut)
async def connect_account(
    workspace_id: str,
    body: ConnectFeishuAccountIn,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    if body.platform != "feishu":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported platform: {body.platform}",
        )
    svc = _service(session, backend, ctx)
    # acting_user_id resolution.
    #
    # ``"self"`` is always allowed: the caller binds a bot that runs as
    # themselves. Any other value is impersonation — the bound bot would
    # run with someone else's permissions for every future IM-triggered
    # message that isn't covered by the per-sender identity gate. We
    # require **workspace admin** to grant that (the identity gate falls
    # back to ``acting_user_id`` when the sender doesn't resolve to a
    # workspace member, so an org-member-only check leaks privilege).
    if body.acting_user_id == "self":
        acting = ctx.user.id
    else:
        caller_ws_role = await MembershipRepository(session).get_role(
            user_id=ctx.user.id, workspace_id=ctx.workspace_id
        )
        if caller_ws_role != Role.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="workspace admin required to impersonate another user",
            )
        # Target must still be an org member — otherwise the bot would
        # run with a stale or external user's identity.
        om_repo = OrganizationMembershipRepository(session)
        target_org_role = await om_repo.get_role(user_id=body.acting_user_id, org_id=ctx.org_id)
        if target_org_role is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="acting_user_id is not a member of this organization",
            )
        acting = body.acting_user_id
    try:
        account = await svc.connect_feishu(
            workspace_id=ctx.workspace_id,
            app_id=body.app_id,
            app_secret=body.app_secret,
            encrypt_key=body.encrypt_key,
            verification_token=body.verification_token,
            domain=body.domain,
            delivery_mode=body.delivery_mode,
            acting_user_id=acting,
        )
    except ValueError as exc:
        # Duplicate app_id (preflight uniqueness in the service raises
        # ValueError). This is a normal client mistake — double-submit,
        # retry, or two operators racing the same registration. Map to
        # 409 Conflict so the caller can distinguish "already exists"
        # from a real 500.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    # Start the long-connection NOW so the bot is live as soon as the API
    # returns 201; otherwise the WebSocket only opens on the next API
    # restart and operators see the account "connected" but silent.
    if account.delivery_mode == "long_connection" and account.enabled:
        starter = getattr(request.app.state, "im_connect_account", None)
        if starter is not None:
            try:
                await starter(account)
            except Exception:
                logger.warning(
                    "[IM ws] long-connection startup failed for {}", account.id, exc_info=True
                )
    return _to_out(account)


@router.get("/accounts", response_model=IMAccountListOut)
async def list_accounts(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountListOut:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    svc = _service(session, backend, ctx)
    accounts = await svc.list_for_workspace(workspace_id=ctx.workspace_id)
    return IMAccountListOut(accounts=[_to_out(a) for a in accounts])


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    workspace_id: str,
    account_id: str,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> None:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    svc = _service(session, backend, ctx)
    # Pass workspace_id so a member of workspace A cannot delete an account
    # that lives in workspace B within the same org.
    await svc.delete(account_id=account_id, workspace_id=ctx.workspace_id)
    # Tear down any live long-connection client so a deleted account stops
    # accepting events immediately, not after the next API restart.
    long_conns = getattr(request.app.state, "im_long_connections", None) or {}
    lc = long_conns.pop(account_id, None)
    if lc is not None:
        try:
            await lc.disconnect()
        except Exception:
            logger.warning(
                "[IM ws] long-connection disconnect failed on delete for {}",
                account_id,
                exc_info=True,
            )
