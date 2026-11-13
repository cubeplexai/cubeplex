"""Org-scoped invite routes.

Create is org-admin scoped (org resolved from the admin session). Accept is
auth-scoped — a logged-in user holding a valid token joins the org at the
invite's role. An invite addressed to an email can be accepted only by that
address. Invite role is limited to ADMIN/MEMBER (never OWNER).
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.dependencies import (
    current_active_user,
    require_org_admin,
    resolve_current_org_id,
)
from cubeplex.db import get_session
from cubeplex.models import Organization, OrgRole, User
from cubeplex.repositories import OrganizationMembershipRepository, OrgInviteTokenRepository
from cubeplex.repositories.org_invite_token import OrgInviteEmailMismatch
from cubeplex.utils.time import utc_isoformat

router = APIRouter(tags=["org-invites"])

_ADMIN_ROUTER = APIRouter(prefix="/admin/orgs/invites", tags=["org-invites"])
_ACCEPT_ROUTER = APIRouter(prefix="/orgs/invites", tags=["org-invites"])

_ASSIGNABLE_ORG_ROLES = {OrgRole.ADMIN, OrgRole.MEMBER}


class CreateOrgInviteRequest(BaseModel):
    role: str
    email: str | None = None

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not cleaned:
            return None
        from email_validator import EmailNotValidError, validate_email

        try:
            validate_email(cleaned, check_deliverability=False)
        except EmailNotValidError as exc:
            raise ValueError("invalid_email") from exc
        return cleaned


class OrgInviteOut(BaseModel):
    token: str
    expires_at: str
    role: str
    invite_url: str
    email: str | None = None
    email_sent: bool = False


def _invite_url(token: str) -> str:
    from cubeplex.config import config

    base_url = str(config.get("frontend_base_url", "http://localhost:3000")).rstrip("/")
    return f"{base_url}/orgs/invites/accept?token={token}"


@_ADMIN_ROUTER.post("", response_model=OrgInviteOut, status_code=status.HTTP_201_CREATED)
async def create_org_invite(
    body: Annotated[CreateOrgInviteRequest, Body()],
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrgInviteOut:
    try:
        role = OrgRole(body.role)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_role"
        ) from None
    if role not in _ASSIGNABLE_ORG_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role_not_assignable")
    org_id = await resolve_current_org_id(user, session)
    om_repo = OrganizationMembershipRepository(session)
    if body.email is not None:
        existing_user = (
            await session.execute(select(User).where(func.lower(User.email) == body.email))
        ).scalar_one_or_none()
        if existing_user is not None and (
            await om_repo.get_role(user_id=existing_user.id, org_id=org_id)
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already_member")
    org = await session.get(Organization, org_id)
    org_name = org.name if org is not None else ""
    tok = await OrgInviteTokenRepository(session).issue(
        org_id=org_id,
        role=role,
        created_by=user.id,
        email=body.email,
    )
    invite_url = _invite_url(tok.token)
    email_sent = False
    if body.email is not None:
        from cubeplex.services.email import get_email_service

        try:
            await get_email_service().send(
                to=body.email,
                subject=f"You're invited to {org_name} on CubePlex",
                template="org_invite",
                context={
                    "invite_url": invite_url,
                    "org_name": org_name,
                    "role": role.value,
                },
            )
            email_sent = True
        except Exception:
            logger.warning("Failed to send org invite email to {}", body.email)
    return OrgInviteOut(
        token=tok.token,
        expires_at=utc_isoformat(tok.expires_at),
        role=tok.role,
        invite_url=invite_url,
        email=tok.email,
        email_sent=email_sent,
    )


class AcceptOrgInviteRequest(BaseModel):
    token: str


class AcceptOrgInviteResponse(BaseModel):
    org_id: str
    role: str


@_ACCEPT_ROUTER.post("/accept", response_model=AcceptOrgInviteResponse)
async def accept_org_invite(
    body: Annotated[AcceptOrgInviteRequest, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AcceptOrgInviteResponse:
    try:
        tok = await OrgInviteTokenRepository(session).consume(body.token, acceptor_email=user.email)
    except OrgInviteEmailMismatch:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invite_email_mismatch",
        ) from None
    if tok is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invite_invalid_or_expired",
        )
    om_repo = OrganizationMembershipRepository(session)
    existing = await om_repo.get_role(user_id=user.id, org_id=tok.org_id)
    if existing is None:
        await om_repo.grant(user_id=user.id, org_id=tok.org_id, role=OrgRole(tok.role))
    return AcceptOrgInviteResponse(org_id=tok.org_id, role=tok.role)


router.include_router(_ADMIN_ROUTER)
router.include_router(_ACCEPT_ROUTER)
