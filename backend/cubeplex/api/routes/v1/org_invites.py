"""Org-scoped invite routes.

Create is org-admin scoped (org resolved from the admin session). Accept is
auth-scoped — any logged-in user holding a valid token joins the org at the
invite's role. Invite role is limited to ADMIN/MEMBER (never OWNER).
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.dependencies import (
    current_active_user,
    require_org_admin,
    resolve_current_org_id,
)
from cubeplex.db import get_session
from cubeplex.models import OrgRole, User
from cubeplex.repositories import OrganizationMembershipRepository, OrgInviteTokenRepository
from cubeplex.utils.time import utc_isoformat

router = APIRouter(tags=["org-invites"])

_ADMIN_ROUTER = APIRouter(prefix="/admin/orgs/invites", tags=["org-invites"])
_ACCEPT_ROUTER = APIRouter(prefix="/orgs/invites", tags=["org-invites"])

_ASSIGNABLE_ORG_ROLES = {OrgRole.ADMIN, OrgRole.MEMBER}


class CreateOrgInviteRequest(BaseModel):
    role: str


class OrgInviteOut(BaseModel):
    token: str
    expires_at: str
    role: str
    invite_url: str


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
    tok = await OrgInviteTokenRepository(session).issue(
        org_id=org_id, role=role, created_by=user.id
    )
    from cubeplex.config import config

    base_url = str(config.get("frontend_base_url", "http://localhost:3000")).rstrip("/")
    invite_url = f"{base_url}/orgs/invites/accept?token={tok.token}"
    return OrgInviteOut(
        token=tok.token,
        expires_at=utc_isoformat(tok.expires_at),
        role=tok.role,
        invite_url=invite_url,
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
    tok = await OrgInviteTokenRepository(session).consume(body.token)
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
