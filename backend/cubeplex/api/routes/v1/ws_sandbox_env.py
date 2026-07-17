"""Workspace- and user-scope sandbox env vault routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.sandbox_env import (
    CreateUserEnvIn,
    CreateWorkspaceEnvIn,
    EnvEntryListOut,
    EnvEntryOut,
    UpdateEntryIn,
)
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_admin, require_member
from cubeplex.credentials.dependencies import get_encryption_backend
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.db.session import get_session
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

router = APIRouter(prefix="/ws/{workspace_id}/sandbox-env", tags=["ws-sandbox-env"])


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
        )
    except SandboxEnvConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (SandboxEnvShapeError, HostPatternError) as exc:
        raise HTTPException(400, str(exc)) from exc
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    assert row is not None
    return await _entry_with_warnings(session, org_id=ctx.org_id, row=row)


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
        )
    except SandboxEnvConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (SandboxEnvShapeError, HostPatternError) as exc:
        raise HTTPException(400, str(exc)) from exc
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    assert row is not None
    return await _entry_with_warnings(session, org_id=ctx.org_id, row=row)


@router.get("/workspace", response_model=EnvEntryListOut)
async def list_workspace_env(
    workspace_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> EnvEntryListOut:
    rows = await SandboxEnvRepository(session, org_id=ctx.org_id).list_scope(
        scope="workspace", workspace_id=workspace_id
    )
    return EnvEntryListOut(
        entries=[EnvEntryOut(**r.model_dump(include=set(EnvEntryOut.model_fields))) for r in rows]
    )


@router.get("/me", response_model=EnvEntryListOut)
async def list_user_env(
    workspace_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> EnvEntryListOut:
    rows = await SandboxEnvRepository(session, org_id=ctx.org_id).list_scope(
        scope="user", workspace_id=workspace_id, user_id=ctx.user.id
    )
    return EnvEntryListOut(
        entries=[EnvEntryOut(**r.model_dump(include=set(EnvEntryOut.model_fields))) for r in rows]
    )


@router.delete("/workspace/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace_env(
    workspace_id: str,
    entry_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> None:
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    if not (
        row is not None
        and row.org_id == ctx.org_id
        and row.scope == "workspace"
        and row.workspace_id == workspace_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    await _service(session, backend, ctx).delete_entry(entry_id=entry_id)


@router.delete("/me/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_env(
    workspace_id: str,
    entry_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    if not (
        row is not None
        and row.scope == "user"
        and row.workspace_id == workspace_id
        and row.user_id == ctx.user.id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    await _service(session, backend, ctx).delete_entry(entry_id=entry_id)


@router.patch("/workspace/{entry_id}", response_model=EnvEntryOut)
async def update_workspace_env(
    workspace_id: str,
    entry_id: str,
    body: UpdateEntryIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> EnvEntryOut:
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    if not (
        row is not None
        and row.org_id == ctx.org_id
        and row.scope == "workspace"
        and row.workspace_id == workspace_id
    ):
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


@router.patch("/me/{entry_id}", response_model=EnvEntryOut)
async def update_user_env(
    workspace_id: str,
    entry_id: str,
    body: UpdateEntryIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> EnvEntryOut:
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    if not (
        row is not None
        and row.scope == "user"
        and row.workspace_id == workspace_id
        and row.user_id == ctx.user.id
    ):
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
