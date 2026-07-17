"""Org-scope sandbox env vault routes (org admins only)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.sandbox_env import (
    CreateOrgEnvIn,
    EnvEntryListOut,
    EnvEntryOut,
    UpdateEntryIn,
)
from cubeplex.auth.context import RequestContext
from cubeplex.credentials.dependencies import get_encryption_backend
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.db.session import get_session
from cubeplex.mcp.dependencies import get_admin_request_context
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.repositories.sandbox_policy import SandboxPolicyRepository
from cubeplex.sandbox_env.host_rules import HostPatternError
from cubeplex.services.credential import CredentialService
from cubeplex.services.sandbox_env import (
    SandboxEnvConflictError,
    SandboxEnvService,
    SandboxEnvShapeError,
)
from cubeplex.services.sandbox_policy_conflicts import deny_targets_for_cred

router = APIRouter(prefix="/admin/sandbox-env", tags=["admin-sandbox-env"])


async def _entry_with_warnings(session: AsyncSession, *, org_id: str, row: object) -> EnvEntryOut:
    """Build EnvEntryOut + attach OQ-6 deny-host warnings from the org policy."""
    hosts = getattr(row, "hosts", None)
    policy = await SandboxPolicyRepository(session, org_id=org_id).get()
    denied = deny_targets_for_cred(hosts, policy.network_rules if policy is not None else None)
    warnings = [
        f"host {h} is denied by the current sandbox policy; outbound calls will be blocked"
        for h in denied
    ]
    base = row.model_dump(include=set(EnvEntryOut.model_fields))  # type: ignore[attr-defined]
    base["warnings"] = warnings
    return EnvEntryOut(**base)


def _service(
    session: AsyncSession, backend: EncryptionBackend, ctx: RequestContext
) -> SandboxEnvService:
    cred = CredentialService(
        CredentialRepository(session, org_id=ctx.org_id),
        backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )
    return SandboxEnvService(
        repo=SandboxEnvRepository(session, org_id=ctx.org_id),
        credentials=cred,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )


@router.post("", response_model=EnvEntryOut, status_code=status.HTTP_201_CREATED)
async def create_org_env(
    body: CreateOrgEnvIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> EnvEntryOut:
    svc = _service(session, backend, ctx)
    try:
        entry_id = await svc.create_entry(
            env_name=body.env_name,
            is_secret=body.is_secret,
            scope="org",
            workspace_id=None,
            user_id=None,
            hosts=body.hosts,
            header_names=body.header_names,
            secret_value=body.secret_value,
        )
    except SandboxEnvConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (SandboxEnvShapeError, HostPatternError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    assert row is not None
    return await _entry_with_warnings(session, org_id=ctx.org_id, row=row)


@router.get("", response_model=EnvEntryListOut)
async def list_org_env(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> EnvEntryListOut:
    rows = await SandboxEnvRepository(session, org_id=ctx.org_id).list_scope(scope="org")
    return EnvEntryListOut(
        entries=[EnvEntryOut(**r.model_dump(include=set(EnvEntryOut.model_fields))) for r in rows]
    )


@router.patch("/{entry_id}", response_model=EnvEntryOut)
async def update_org_env(
    entry_id: str,
    body: UpdateEntryIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> EnvEntryOut:
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    if row is None or row.scope != "org":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    try:
        await _service(session, backend, ctx).update_entry(
            entry_id=entry_id,
            hosts=body.hosts,
            header_names=body.header_names,
            update_header_names="header_names" in body.model_fields_set,
            secret_value=body.secret_value,
        )
    except (SandboxEnvShapeError, HostPatternError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    updated = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    assert updated is not None
    return await _entry_with_warnings(session, org_id=ctx.org_id, row=updated)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org_env(
    entry_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> None:
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    if row is None or row.scope != "org":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    await _service(session, backend, ctx).delete_entry(entry_id=entry_id)
