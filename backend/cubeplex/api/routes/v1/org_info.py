"""Public org lookup for the login page.

Stays in core even though it reports on SSO: the login page calls it to decide
whether to offer an SSO button, so on a deployment without the licensed package
it has to answer rather than 404. It reads the core-owned ``sso_connection``
table, which on such a deployment is empty — so the honest answer there is
``sso_enabled: false``, which is exactly what it returns.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.db import get_session
from cubeplex.models.organization import Organization
from cubeplex.models.sso_connection import SSOConnection

router = APIRouter(prefix="/auth", tags=["org-info"])


class OrgInfoResponse(BaseModel):
    org_name: str
    sso_enabled: bool
    sso_protocol: str | None = None


@router.get("/org-info/{org_slug}", response_model=OrgInfoResponse)
async def get_org_info(
    org_slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrgInfoResponse:
    org = (
        await session.execute(
            select(Organization).where(
                Organization.slug == org_slug  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(404, detail="org_not_found")
    conn = (
        await session.execute(
            select(SSOConnection).where(
                SSOConnection.org_id == org.id,  # type: ignore[arg-type]
                SSOConnection.status.in_(["active", "testing"]),  # type: ignore[attr-defined]
            )
        )
    ).scalar_one_or_none()
    return OrgInfoResponse(
        org_name=org.name,
        sso_enabled=conn is not None,
        sso_protocol=conn.protocol if conn else None,
    )
