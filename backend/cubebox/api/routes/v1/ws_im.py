"""Workspace-scope IM connector routes (Task 15)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.routes.v1._im_runtime import build_im_list_out
from cubebox.api.schemas.im_connector import (
    ConnectDingtalkAccountIn,
    ConnectDiscordAccountIn,
    ConnectFeishuAccountIn,
    ConnectIMAccountIn,
    ConnectSlackAccountIn,
    ConnectTeamsAccountIn,
    IdentityLinkListOut,
    IdentityLinkOut,
    IMAccountListOut,
    IMAccountOut,
    ImRuntimeStatus,
)
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.credentials.dependencies import (
    build_credential_service,
    get_encryption_backend,
)
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db.session import get_session
from cubebox.im.bot_settings import IMBotSettings, load_bot_settings
from cubebox.models.im_connector import IMConnectorAccount, IMIdentityLink
from cubebox.models.membership import Role
from cubebox.models.user import User
from cubebox.repositories.membership import MembershipRepository
from cubebox.repositories.organization_membership import OrganizationMembershipRepository
from cubebox.services.im_connector import IMConnectorService
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/ws/{workspace_id}/im", tags=["ws-im"])


def _service(
    session: AsyncSession,
    backend: EncryptionBackend,
    ctx: RequestContext,
) -> IMConnectorService:
    creds = build_credential_service(session, backend, org_id=ctx.org_id, actor_user_id=ctx.user.id)
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


async def _resolve_acting_user(
    acting_user_id: str,
    ctx: RequestContext,
    session: AsyncSession,
) -> str:
    # ``"self"`` is always allowed: the caller binds a bot that runs as
    # themselves. Any other value is impersonation — the bound bot would
    # run with someone else's permissions for every future IM-triggered
    # message that isn't covered by the per-sender identity gate. We
    # require **workspace admin** to grant that (the identity gate falls
    # back to ``acting_user_id`` when the sender doesn't resolve to a
    # workspace member, so an org-member-only check leaks privilege).
    if acting_user_id == "self":
        return ctx.user.id
    caller_ws_role = await MembershipRepository(session).get_role(
        user_id=ctx.user.id, workspace_id=ctx.workspace_id
    )
    if caller_ws_role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace admin required to impersonate another user",
        )
    om_repo = OrganizationMembershipRepository(session)
    target_org_role = await om_repo.get_role(user_id=acting_user_id, org_id=ctx.org_id)
    if target_org_role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="acting_user_id is not a member of this organization",
        )
    return acting_user_id


async def _connect_feishu(
    body: ConnectFeishuAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
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
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if account.delivery_mode == "long_connection" and account.enabled:
        starter = getattr(request.app.state, "im_connect_account", None)
        if starter is not None:
            try:
                await starter(account)
            except Exception:
                logger.opt(exception=True).warning(
                    "[IM ws] long-connection startup failed for {}", account.id
                )
    return _to_out(account)


async def _connect_discord(
    body: ConnectDiscordAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_discord(
            workspace_id=ctx.workspace_id,
            bot_token=body.bot_token,
            application_id=body.application_id,
            acting_user_id=acting,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] discord gateway startup failed for {}", account.id
            )
    return _to_out(account)


async def _connect_slack(
    body: ConnectSlackAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_slack(
            workspace_id=ctx.workspace_id,
            bot_token=body.bot_token,
            app_token=body.app_token,
            acting_user_id=acting,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] slack gateway startup failed for {}", account.id
            )
    return _to_out(account)


async def _connect_dingtalk(
    body: ConnectDingtalkAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_dingtalk(
            workspace_id=ctx.workspace_id,
            app_key=body.app_key,
            app_secret=body.app_secret,
            acting_user_id=acting,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] dingtalk gateway startup failed for {}",
                account.id,
            )
    return _to_out(account)


async def _connect_teams(
    body: ConnectTeamsAccountIn,
    request: Request,
    ctx: RequestContext,
    session: AsyncSession,
    backend: EncryptionBackend,
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = await _resolve_acting_user(body.acting_user_id, ctx, session)
    try:
        account = await svc.connect_teams(
            workspace_id=ctx.workspace_id,
            app_id=body.app_id,
            app_secret=body.app_secret,
            tenant_id=body.tenant_id,
            acting_user_id=acting,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    starter = getattr(request.app.state, "im_connect_account", None)
    if starter is not None and account.enabled:
        try:
            await starter(account)
        except Exception:
            logger.opt(exception=True).warning("[IM ws] teams app init failed for {}", account.id)
    return _to_out(account)


@router.post("/accounts", status_code=status.HTTP_201_CREATED, response_model=IMAccountOut)
async def connect_account(
    workspace_id: str,
    body: ConnectIMAccountIn,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")

    if isinstance(body, ConnectFeishuAccountIn):
        return await _connect_feishu(body, request, ctx, session, backend)
    elif isinstance(body, ConnectDiscordAccountIn):
        return await _connect_discord(body, request, ctx, session, backend)
    elif isinstance(body, ConnectSlackAccountIn):
        return await _connect_slack(body, request, ctx, session, backend)
    elif isinstance(body, ConnectDingtalkAccountIn):
        return await _connect_dingtalk(body, request, ctx, session, backend)
    elif isinstance(body, ConnectTeamsAccountIn):
        return await _connect_teams(body, request, ctx, session, backend)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported platform",
        )


@router.get("/accounts", response_model=IMAccountListOut)
async def list_accounts(
    workspace_id: str,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountListOut:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    svc = _service(session, backend, ctx)
    accounts = await svc.list_for_workspace(workspace_id=ctx.workspace_id)
    long_conns = getattr(request.app.state, "im_long_connections", None) or {}
    gateways = getattr(request.app.state, "im_gateways", None) or {}
    return await build_im_list_out(
        svc=svc, session=session, long_conns=long_conns, gateways=gateways, accounts=accounts
    )


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
    # Tear down any live connection so a deleted account stops accepting
    # events immediately, not after the next API restart.
    long_conns = getattr(request.app.state, "im_long_connections", None) or {}
    lc = long_conns.pop(account_id, None)
    if lc is not None:
        try:
            await lc.disconnect()
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] long-connection disconnect failed on delete for {}",
                account_id,
            )
    gateways = getattr(request.app.state, "im_gateways", None) or {}
    gw = gateways.pop(account_id, None)
    if gw is not None:
        try:
            await gw.stop()
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] gateway stop failed on delete for {}",
                account_id,
            )


@router.post("/accounts/{account_id}/disable", response_model=IMAccountOut)
async def disable_workspace_account(
    workspace_id: str,
    account_id: str,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    """Workspace-scope disable. The admin route remains for org-wide ops."""
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    role = await MembershipRepository(session).get_role(
        user_id=ctx.user.id, workspace_id=ctx.workspace_id
    )
    if role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace admin required",
        )
    svc = _service(session, backend, ctx)
    account = await svc.get(account_id=account_id, workspace_id=ctx.workspace_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    updated = await svc.set_enabled(account_id=account_id, enabled=False)
    assert updated is not None
    # Drop any live connection so the bot stops responding immediately.
    long_conns = getattr(request.app.state, "im_long_connections", None) or {}
    lc = long_conns.pop(account_id, None)
    if lc is not None:
        try:
            await lc.disconnect()
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] long-conn disconnect failed on disable for {}",
                account_id,
            )
    gateways = getattr(request.app.state, "im_gateways", None) or {}
    gw = gateways.pop(account_id, None)
    if gw is not None:
        try:
            await gw.stop()
        except Exception:
            logger.opt(exception=True).warning(
                "[IM ws] gateway stop failed on disable for {}",
                account_id,
            )
    return _to_out(updated)


@router.post("/accounts/{account_id}/enable", response_model=IMAccountOut)
async def enable_workspace_account(
    workspace_id: str,
    account_id: str,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    """Workspace-scope enable. Spins up the long-conn inline."""
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    role = await MembershipRepository(session).get_role(
        user_id=ctx.user.id, workspace_id=ctx.workspace_id
    )
    if role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace admin required",
        )
    svc = _service(session, backend, ctx)
    account = await svc.get(account_id=account_id, workspace_id=ctx.workspace_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    updated = await svc.set_enabled(account_id=account_id, enabled=True)
    assert updated is not None
    if updated.delivery_mode in ("long_connection", "gateway", "stream"):
        starter = getattr(request.app.state, "im_connect_account", None)
        if starter is not None:
            try:
                await starter(updated)
            except Exception:
                logger.opt(exception=True).warning(
                    "[IM ws] long-conn startup failed on enable for {}",
                    account_id,
                )
    return _to_out(updated)


# ---------------------------------------------------------------------------
# Account-level bot settings (routing + topic mode)
# ---------------------------------------------------------------------------


@router.get("/accounts/{account_id}/settings", response_model=IMBotSettings)
async def get_bot_settings(
    workspace_id: str,
    account_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMBotSettings:
    """Read the bot's account-level routing/topic settings (defaults if unset)."""
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    svc = _service(session, backend, ctx)
    account = await svc.get(account_id=account_id, workspace_id=ctx.workspace_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return load_bot_settings(account.config)


@router.put("/accounts/{account_id}/settings", response_model=IMBotSettings)
async def update_bot_settings(
    workspace_id: str,
    account_id: str,
    body: IMBotSettings,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMBotSettings:
    """Update the bot's routing/topic settings. Admin-only (mutates behavior)."""
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    role = await MembershipRepository(session).get_role(
        user_id=ctx.user.id, workspace_id=ctx.workspace_id
    )
    if role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace admin required",
        )
    if body.routing_mode == "shared" and body.sandbox_mode is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sandbox_mode is required when routing_mode is shared",
        )
    svc = _service(session, backend, ctx)
    updated = await svc.update_bot_settings(
        account_id=account_id, settings=body, workspace_id=ctx.workspace_id
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return load_bot_settings(updated.config)


@router.get(
    "/accounts/{account_id}/identity-links",
    response_model=IdentityLinkListOut,
)
async def list_identity_links(
    workspace_id: str,
    account_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IdentityLinkListOut:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    rows = (
        await session.execute(
            select(  # type: ignore[call-overload]
                IMIdentityLink, User.email, User.display_name
            )
            .join(User, IMIdentityLink.user_id == User.id)
            .where(
                IMIdentityLink.account_id == account_id,
                IMIdentityLink.workspace_id == ctx.workspace_id,
            )
            .order_by(IMIdentityLink.created_at.desc())  # type: ignore[attr-defined]
        )
    ).all()
    return IdentityLinkListOut(
        links=[
            IdentityLinkOut(
                id=link.id,
                im_user_id=link.im_user_id,
                user_id=link.user_id,
                user_email=email,
                user_display_name=display_name or "",
                created_at=utc_isoformat(link.created_at),
            )
            for link, email, display_name in rows
        ]
    )

