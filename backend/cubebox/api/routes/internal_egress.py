"""Internal egress secret-exchange endpoint (sidecar-authenticated)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.dependencies import get_encryption_backend
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db import get_session
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.egress_ref import EgressRefRepository
from cubebox.sandbox_env.exchange_auth import SidecarAuthenticator
from cubebox.sandbox_env.placeholder import PLACEHOLDER_RE
from cubebox.services.credential import CredentialService
from cubebox.services.egress_exchange import EgressExchangeError, EgressExchangeService

router = APIRouter(prefix="/internal/egress", tags=["internal-egress"])


class ExchangeIn(BaseModel):
    placeholder: str
    host: str

    @field_validator("placeholder")
    @classmethod
    def validate_placeholder(cls, v: str) -> str:
        if not PLACEHOLDER_RE.fullmatch(v):
            raise ValueError("invalid placeholder format")
        return v


class ExchangeOut(BaseModel):
    secret: str
    header_names: list[str] | None = None


def get_sidecar_authenticator(request: Request) -> SidecarAuthenticator:
    # Built once at startup and stored on app.state (see app.py wiring).
    return request.app.state.sidecar_authenticator  # type: ignore[no-any-return]


@router.post("/exchange", response_model=ExchangeOut)
async def exchange(
    body: ExchangeIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    authenticator: Annotated[SidecarAuthenticator, Depends(get_sidecar_authenticator)],
) -> ExchangeOut:
    try:
        identity = await authenticator.verify(request)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sidecar auth failed") from exc

    svc = EgressExchangeService(
        ref_repo=EgressRefRepository(session),
        credentials_factory=lambda org_id: CredentialService(
            CredentialRepository(session, org_id=org_id),
            backend,
            org_id=org_id,
            actor_user_id=None,
        ),
    )
    try:
        secret, header_names = await svc.exchange(
            identity=identity, placeholder=body.placeholder, host=body.host
        )
    except EgressExchangeError as exc:
        # Fail closed; do not leak which check failed.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "exchange denied") from exc
    return ExchangeOut(secret=secret, header_names=header_names)
