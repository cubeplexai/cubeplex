"""Org-scope sandbox env vault routes (org admins only)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.sandbox_env import (
    CreateOrgEnvIn,
    EnvEntryListOut,
    EnvEntryOut,
    UpdateSecretValueIn,
)
from cubebox.auth.context import RequestContext
from cubebox.credentials.dependencies import get_encryption_backend
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db.session import get_session
from cubebox.mcp.dependencies import get_admin_request_context
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.sandbox_env import SandboxEnvRepository
from cubebox.sandbox_env.host_rules import HostPatternError
from cubebox.services.credential import CredentialService
from cubebox.services.sandbox_env import (
    SandboxEnvConflictError,
    SandboxEnvService,
    SandboxEnvShapeError,
)

router = APIRouter(prefix="/admin/sandbox-env", tags=["admin-sandbox-env"])


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
            plain_value=body.plain_value,
        )
    except SandboxEnvConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (SandboxEnvShapeError, HostPatternError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    assert row is not None
    return EnvEntryOut(**row.model_dump(include=set(EnvEntryOut.model_fields)))


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
async def rotate_org_env(
    entry_id: str,
    body: UpdateSecretValueIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> EnvEntryOut:
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    if row is None or row.scope != "org":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    try:
        await _service(session, backend, ctx).update_secret_value(
            entry_id=entry_id, secret_value=body.secret_value
        )
    except SandboxEnvShapeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    updated = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    assert updated is not None
    return EnvEntryOut(**updated.model_dump(include=set(EnvEntryOut.model_fields)))


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
