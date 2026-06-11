"""Workspace-scope IM connector routes (Task 15)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
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
from cubebox.repositories.membership import MembershipRepository
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
    # acting_user_id must be the caller OR another member of the same
    # workspace — without this check, any member could create a connector
    # acting as a different user (e.g. org admin), causing every IM-triggered
    # run for that account to execute under the impersonated user's identity
    # in RunContext.
    if body.acting_user_id == "self":
        acting = ctx.user.id
    else:
        member_repo = MembershipRepository(session)
        role = await member_repo.get_role(
            user_id=body.acting_user_id, workspace_id=ctx.workspace_id
        )
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="acting_user_id is not a member of this workspace",
            )
        acting = body.acting_user_id
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
