"""IM identity link confirmation endpoint.

Workspace-neutral, authenticated. The workspace comes from the JWT
token (not the URL path); the user comes from the auth cookie.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.dependencies import current_active_user
from cubebox.db.session import get_session
from cubebox.im.link import LinkClaims, verify_link_token
from cubebox.models.im_connector import IMConnectorAccount, IMIdentityLink
from cubebox.models.membership import Membership
from cubebox.models.user import User

router = APIRouter(prefix="/im/link", tags=["im-link"])


class _ConfirmBody(BaseModel):
    token: str


class _ConfirmResult(BaseModel):
    ok: bool
    platform: str = ""
    account_id: str = ""


def _get_jwt_secret() -> str:
    from cubebox.config import config

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
        raise HTTPException(status_code=400, detail="IM 账号不存在。")

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
        raise HTTPException(status_code=409, detail="绑定冲突，请重试。") from None


@router.post("/confirm")
async def confirm_im_link(
    body: Annotated[_ConfirmBody, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> _ConfirmResult:
    secret = _get_jwt_secret()
    try:
        claims = verify_link_token(body.token, secret=secret)
    except ValueError:
        raise HTTPException(status_code=400, detail="链接无效或已过期，请重新发起绑定。") from None

    if user.email.strip().lower() != claims.email:
        raise HTTPException(
            status_code=403,
            detail=f"请使用 {claims.email} 登录后重试。",
        )

    is_member = await _check_membership(session, user.id, claims.workspace_id)
    if not is_member:
        raise HTTPException(
            status_code=403,
            detail="你不是该工作区的成员，请联系工作区管理员将你添加后重试。",
        )

    await _upsert_identity_link(session, claims, user.id)
    logger.info(
        "[IM link] linked im_user={} to user={} (account={})",
        claims.im_user_id,
        user.id,
        claims.account_id,
    )
    return _ConfirmResult(ok=True, platform=claims.platform, account_id=claims.account_id)
