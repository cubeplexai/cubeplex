"""Post-registration onboarding: provision the caller's first org/workspace.

Mode is inferred from the caller's current memberships — no `mode` param.
Full = no org yet (needs org_name + org_slug + workspace_name).
Workspace-only = has an org, no workspace (needs workspace_name).
Already onboarded = 409 onboarding_not_required.
"""

import re
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.dependencies import current_active_user
from cubeplex.db import get_session
from cubeplex.models import Membership, OrganizationMembership, User

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


class OnboardingRequest(BaseModel):
    org_name: str | None = Field(default=None, min_length=2, max_length=64)
    org_slug: str | None = Field(default=None, max_length=32)
    workspace_name: str = Field(min_length=1, max_length=64)

    @field_validator("org_slug")
    @classmethod
    def _check_slug(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) < 3:
            raise ValueError("slug_too_short")
        if not _SLUG_RE.match(v):
            raise ValueError("slug_invalid_format")
        return v


class OnboardingResponse(BaseModel):
    workspace_id: str


@router.post("", response_model=OnboardingResponse, status_code=status.HTTP_201_CREATED)
async def complete_onboarding(
    request: Request,
    body: Annotated[OnboardingRequest, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OnboardingResponse:
    from cubeplex.auth.users import _bootstrap_org_and_workspace, _bootstrap_workspace_in_org

    # Any org membership?
    org_rows = (
        (
            await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == user.id  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    # Any workspace membership?
    ws_rows = (
        (
            await session.execute(
                select(Membership).where(Membership.user_id == user.id)  # type: ignore[arg-type]
            )
        )
        .scalars()
        .all()
    )

    if ws_rows:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="onboarding_not_required")

    try:
        if not org_rows:
            # Full mode.
            if not body.org_name or not body.org_slug:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="org_name_and_slug_required",
                )
            _, ws = await _bootstrap_org_and_workspace(
                session,
                user_id=user.id,
                org_name=body.org_name,
                org_slug=body.org_slug,
                workspace_name=body.workspace_name,
            )
        else:
            # Workspace-only mode: caller already in an org.
            org_id = org_rows[0].org_id
            ws = await _bootstrap_workspace_in_org(
                session,
                user_id=user.id,
                org_id=org_id,
                workspace_name=body.workspace_name,
            )
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="slug_taken") from exc
    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ONBOARDING_FAILED",
        ) from exc

    await session.commit()
    return OnboardingResponse(workspace_id=ws.id)
