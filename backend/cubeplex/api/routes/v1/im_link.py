"""IM identity link confirmation endpoint.

Workspace-neutral, authenticated. The workspace comes from the JWT
token (not the URL path); the user comes from the auth cookie.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.dependencies import current_active_user
from cubeplex.credentials.dependencies import (
    build_credential_service,
    get_encryption_backend,
)
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.db.session import get_session
from cubeplex.im.link import LinkClaims, verify_link_token
from cubeplex.models.im_connector import IMConnectorAccount, IMIdentityLink
from cubeplex.models.membership import Membership
from cubeplex.models.user import User

router = APIRouter(prefix="/im/link", tags=["im-link"])


class _ConfirmBody(BaseModel):
    token: str


class _ConfirmResult(BaseModel):
    ok: bool
    platform: str = ""
    account_id: str = ""


def _get_jwt_secret() -> str:
    from cubeplex.config import config

    return str(config.get("auth.jwt_secret", "CHANGE_ME"))


async def _check_membership(session: AsyncSession, user_id: str, workspace_id: str) -> bool:
    row = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user_id,  # type: ignore[arg-type]
                Membership.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def _upsert_identity_link(
    session: AsyncSession,
    claims: LinkClaims,
    user_id: str,
) -> None:
    account = (
        await session.execute(
            select(IMConnectorAccount).where(
                IMConnectorAccount.id == claims.account_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "account_not_found", "message": "IM account not found."},
        )

    existing = (
        await session.execute(
            select(IMIdentityLink).where(
                IMIdentityLink.account_id == claims.account_id,  # type: ignore[arg-type]
                IMIdentityLink.im_user_id == claims.im_user_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.user_id = user_id
        session.add(existing)
    else:
        link = IMIdentityLink(
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            account_id=claims.account_id,
            im_user_id=claims.im_user_id,
            user_id=user_id,
        )
        session.add(link)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "link_conflict", "message": "Link conflict, please retry."},
        ) from None


@router.post("/confirm")
async def confirm_im_link(
    body: Annotated[_ConfirmBody, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> _ConfirmResult:
    secret = _get_jwt_secret()
    try:
        claims = verify_link_token(body.token, secret=secret)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_token", "message": "Link expired or invalid."},
        ) from None

    if user.email.strip().lower() != claims.email:
        raise HTTPException(
            status_code=403,
            detail={"code": "email_mismatch", "message": "Email mismatch."},
        )

    is_member = await _check_membership(session, user.id, claims.workspace_id)
    if not is_member:
        raise HTTPException(
            status_code=403,
            detail={"code": "not_member", "message": "Not a workspace member."},
        )

    await _upsert_identity_link(session, claims, user.id)
    logger.info(
        "[IM link] linked im_user={} to user={} (account={})",
        claims.im_user_id,
        user.id,
        claims.account_id,
    )

    # Best-effort: send a confirmation back to the originating chat so the
    # user sees feedback in IM too. Failure here must not flip the API
    # response — the link is already persisted.
    if claims.platform == "feishu" and claims.chat_id:
        try:
            await _send_feishu_link_success_notice(
                session=session,
                backend=backend,
                claims=claims,
                display_name=user.display_name or user.email,
            )
        except Exception:
            logger.opt(exception=True).warning(
                "[IM link] post-confirm notice failed for account={}",
                claims.account_id,
            )

    return _ConfirmResult(ok=True, platform=claims.platform, account_id=claims.account_id)


async def _send_feishu_link_success_notice(
    *,
    session: AsyncSession,
    backend: EncryptionBackend,
    claims: LinkClaims,
    display_name: str,
) -> None:
    """Post '绑定成功' back to the Feishu chat that issued the /link command."""
    account = (
        await session.execute(
            select(IMConnectorAccount).where(
                IMConnectorAccount.id == claims.account_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if account is None:
        return

    cred_service = build_credential_service(
        session,
        backend,
        org_id=account.org_id,
        actor_user_id=None,
    )
    secret_json = await cred_service.get_decrypted(
        credential_id=account.credential_id, requesting_kind="im_bot"
    )
    secrets = json.loads(secret_json)

    try:
        import lark_oapi as _lark
        from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN
    except ImportError:
        logger.warning("[IM link] lark_oapi missing; skipping confirmation notice")
        return

    from cubeplex.im.feishu.connector import FeishuConnector

    domain = LARK_DOMAIN if str(secrets.get("domain", "feishu")) == "lark" else FEISHU_DOMAIN
    client = (
        _lark.Client.builder()
        .app_id(str(secrets["app_id"]))
        .app_secret(str(secrets["app_secret"]))
        .domain(domain)
        .log_level(_lark.LogLevel.WARNING)
        .build()
    )
    connector = FeishuConnector(
        bot_open_id=str(secrets.get("bot_open_id") or "") or None,
        client=client,
    )
    text = f"✅ 绑定成功！已将此账号关联到 cubeplex 用户 {display_name}。"
    await connector.send_to_chat(claims.chat_id, None, text)
