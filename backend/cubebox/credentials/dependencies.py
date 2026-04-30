"""FastAPI DI providers for credential vault services."""

from typing import cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db.session import get_session
from cubebox.repositories.credential import CredentialRepository
from cubebox.services.credential import CredentialService


async def get_encryption_backend(request: Request) -> EncryptionBackend:
    """Return the process-wide encryption backend stored on app.state."""
    return cast(EncryptionBackend, request.app.state.encryption_backend)


def build_credential_service(
    session: AsyncSession,
    backend: EncryptionBackend,
    *,
    org_id: str,
    actor_user_id: str,
) -> CredentialService:
    repo = CredentialRepository(session, org_id=org_id)
    return CredentialService(
        repo,
        backend,
        org_id=org_id,
        actor_user_id=actor_user_id,
    )


async def get_credential_service(
    session: AsyncSession = Depends(get_session),
    backend: EncryptionBackend = Depends(get_encryption_backend),
    ctx: RequestContext = Depends(require_member),
) -> CredentialService:
    return build_credential_service(
        session,
        backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )
