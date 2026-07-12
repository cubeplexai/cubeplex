"""Org-scope sandbox policy routes (org admins only). Org-wide; no ws counterpart."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.sandbox_policy import SandboxPolicyOut, UpdateSandboxPolicyIn
from cubeplex.auth.context import RequestContext
from cubeplex.config import config
from cubeplex.db.session import get_session
from cubeplex.mcp.dependencies import get_admin_request_context
from cubeplex.repositories.sandbox_policy import SandboxPolicyRepository
from cubeplex.services.sandbox_policy import (
    SandboxPolicyResolver,
    SandboxPolicyService,
    SandboxPolicyValidationError,
)
from cubeplex.services.sandbox_policy_conflicts import (
    credential_conflict_warnings,
    list_org_credentials_with_hosts,
)

router = APIRouter(prefix="/admin/sandbox-policy", tags=["admin-sandbox-policy"])


def _default_image() -> str:
    return str(config.get("sandbox.image", "ubuntu:22.04"))


@router.get("", response_model=SandboxPolicyOut)
async def get_sandbox_policy(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> SandboxPolicyOut:
    repo = SandboxPolicyRepository(session, org_id=ctx.org_id)
    eff = await SandboxPolicyResolver(repo, default_image=_default_image()).resolve()
    return SandboxPolicyOut(
        default_image=eff.default_image,
        network_rules=eff.network_rules,
        command_rules=eff.command_rules,
        network_default_action=eff.network_default_action,
        egress_proxy=eff.egress_proxy,
        resource_cpu=eff.resource_cpu,
        resource_memory=eff.resource_memory,
        storage=eff.storage,
        warnings=[],
    )


@router.put("", response_model=SandboxPolicyOut)
async def put_sandbox_policy(
    body: UpdateSandboxPolicyIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> SandboxPolicyOut:
    repo = SandboxPolicyRepository(session, org_id=ctx.org_id)
    svc = SandboxPolicyService(repo)
    try:
        row = await svc.upsert(
            default_image=body.default_image,
            network_rules=body.network_rules,
            command_rules=body.command_rules,
            network_default_action=body.network_default_action,
            egress_proxy=body.egress_proxy,
            resource_cpu=body.resource_cpu,
            resource_memory=body.resource_memory,
            storage=body.storage,
        )
    except SandboxPolicyValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    installed_creds = await list_org_credentials_with_hosts(session, org_id=ctx.org_id)
    warnings = credential_conflict_warnings(row.network_rules, installed_creds)

    return SandboxPolicyOut(
        default_image=row.default_image,
        network_rules=row.network_rules or [],
        command_rules=row.command_rules or [],
        network_default_action=row.network_default_action,
        egress_proxy=row.egress_proxy,
        resource_cpu=row.resource_cpu,
        resource_memory=row.resource_memory,
        storage=row.storage,
        warnings=warnings,
    )
