"""Internal egress secret-exchange endpoint (sidecar-authenticated)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.config import config as app_config
from cubeplex.credentials.dependencies import get_encryption_backend
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.db import get_session
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.egress_ref import EgressRefRepository
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.repositories.sandbox_policy import SandboxPolicyRepository
from cubeplex.sandbox_env.exchange_auth import SidecarAuthenticator
from cubeplex.sandbox_env.placeholder import PLACEHOLDER_RE
from cubeplex.services.credential import CredentialService
from cubeplex.services.egress_exchange import EgressExchangeError, EgressExchangeService
from cubeplex.services.sandbox_policy import SandboxPolicyResolver

router = APIRouter(prefix="/internal/egress", tags=["internal-egress"])


class ProxyConfigOut(BaseModel):
    proxy: str | None = None


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


async def _lookup_org_id_by_sandbox(session: AsyncSession, sandbox_id: str) -> str | None:
    """Unscoped sandbox_id → org_id (the sidecar cert only proves sandbox_id)."""
    from sqlalchemy import select

    stmt = select(UserSandbox).where(
        UserSandbox.sandbox_id == sandbox_id  # type: ignore[arg-type]
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row.org_id if row is not None else None


async def _resolve_egress_proxy_for_org(session: AsyncSession, org_id: str) -> str | None:
    repo = SandboxPolicyRepository(session, org_id=org_id)
    default_image = str(app_config.get("sandbox.image", "ubuntu:22.04"))
    eff = await SandboxPolicyResolver(repo, default_image=default_image).resolve()
    return eff.egress_proxy


async def _resolve_proxy_for_sandbox(session: AsyncSession, *, sandbox_id: str) -> str | None:
    org_id = await _lookup_org_id_by_sandbox(session, sandbox_id)
    if org_id is None:
        return None
    return await _resolve_egress_proxy_for_org(session, org_id)


@router.get("/proxy-config", response_model=ProxyConfigOut)
async def get_proxy_config(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    authenticator: Annotated[SidecarAuthenticator, Depends(get_sidecar_authenticator)],
) -> ProxyConfigOut:
    try:
        identity = await authenticator.verify(request)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sidecar auth failed") from exc
    proxy = await _resolve_proxy_for_sandbox(session, sandbox_id=identity.sandbox_id)
    return ProxyConfigOut(proxy=proxy)


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
        env_var_repo_factory=lambda org_id: SandboxEnvRepository(session, org_id=org_id),
    )
    try:
        secret, header_names = await svc.exchange(
            identity=identity, placeholder=body.placeholder, host=body.host
        )
    except EgressExchangeError as exc:
        # Fail closed; do not leak which check failed.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "exchange denied") from exc
    return ExchangeOut(secret=secret, header_names=header_names)
