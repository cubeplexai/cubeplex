"""Workspace- and user-scope sandbox env vault routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.sandbox_env import CreateUserEnvIn, CreateWorkspaceEnvIn, EnvEntryOut
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_admin, require_member
from cubebox.credentials.dependencies import get_encryption_backend
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db.session import get_session
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.sandbox_env import SandboxEnvRepository
from cubebox.sandbox_env.host_rules import HostPatternError
from cubebox.services.credential import CredentialService
from cubebox.services.sandbox_env import SandboxEnvService, SandboxEnvShapeError

router = APIRouter(prefix="/ws/{workspace_id}/sandbox-env", tags=["ws-sandbox-env"])


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


@router.post("/workspace", response_model=EnvEntryOut, status_code=201)
async def create_workspace_env(
    workspace_id: str,
    body: CreateWorkspaceEnvIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> EnvEntryOut:
    try:
        entry_id = await _service(session, backend, ctx).create_entry(
            env_name=body.env_name,
            is_secret=body.is_secret,
            scope="workspace",
            workspace_id=workspace_id,
            user_id=None,
            hosts=body.hosts,
            header_names=body.header_names,
            secret_value=body.secret_value,
            plain_value=body.plain_value,
        )
    except (SandboxEnvShapeError, HostPatternError) as exc:
        raise HTTPException(400, str(exc)) from exc
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    assert row is not None
    return EnvEntryOut(**row.model_dump(include=set(EnvEntryOut.model_fields)))


@router.post("/me", response_model=EnvEntryOut, status_code=201)
async def create_user_env(
    workspace_id: str,
    body: CreateUserEnvIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> EnvEntryOut:
    try:
        entry_id = await _service(session, backend, ctx).create_entry(
            env_name=body.env_name,
            is_secret=body.is_secret,
            scope="user",
            workspace_id=workspace_id,
            user_id=ctx.user.id,
            hosts=body.hosts,
            header_names=body.header_names,
            secret_value=body.secret_value,
            plain_value=body.plain_value,
        )
    except (SandboxEnvShapeError, HostPatternError) as exc:
        raise HTTPException(400, str(exc)) from exc
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    assert row is not None
    return EnvEntryOut(**row.model_dump(include=set(EnvEntryOut.model_fields)))
