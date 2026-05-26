from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.credentials.encryption import FernetBackend
from cubebox.models import EgressRef
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.egress_ref import EgressRefRepository
from cubebox.sandbox_env.exchange_auth import SidecarIdentity
from cubebox.sandbox_env.placeholder import hash_placeholder, mint_placeholder
from cubebox.services.credential import CredentialService
from cubebox.services.egress_exchange import (
    EgressExchangeError,
    EgressExchangeService,
)
from cubebox.services.sandbox_env import SANDBOX_ENV_KIND


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[EgressExchangeService, str]:
    backend = FernetBackend([Fernet.generate_key()])
    cred = CredentialService(
        CredentialRepository(session, org_id="org-1"),
        backend,
        org_id="org-1",
        actor_user_id="u1",
    )
    cred_id = await cred.create(kind=SANDBOX_ENV_KIND, name="t", plaintext="ghp_real")
    placeholder = mint_placeholder()
    await EgressRefRepository(session).add(
        EgressRef(
            ref_hash=hash_placeholder(placeholder),
            sandbox_id="sbx-1",
            org_id="org-1",
            workspace_id="ws-1",
            user_id="u1",
            run_id="run-1",
            bindings=[
                {
                    "ref_hash": hash_placeholder(placeholder),
                    "env_name": "GITHUB_TOKEN",
                    "hosts": ["api.github.com"],
                    "header_names": None,
                    "credential_id": cred_id,
                }
            ],
        )
    )
    svc = EgressExchangeService(
        ref_repo=EgressRefRepository(session),
        credentials_factory=lambda org_id: CredentialService(
            CredentialRepository(session, org_id=org_id),
            backend,
            org_id=org_id,
            actor_user_id=None,
        ),
    )
    return svc, placeholder


async def test_exchange_returns_secret_for_matching_sandbox_and_host(
    session: AsyncSession,
) -> None:
    svc, placeholder = await _seed(session)
    secret = await svc.exchange(
        identity=SidecarIdentity(sandbox_id="sbx-1"),
        placeholder=placeholder,
        host="api.github.com",
    )
    assert secret == "ghp_real"


async def test_rejects_sandbox_id_mismatch(session: AsyncSession) -> None:
    svc, placeholder = await _seed(session)
    with pytest.raises(EgressExchangeError):
        await svc.exchange(
            identity=SidecarIdentity(sandbox_id="sbx-OTHER"),
            placeholder=placeholder,
            host="api.github.com",
        )


async def test_rejects_non_declared_host(session: AsyncSession) -> None:
    svc, placeholder = await _seed(session)
    with pytest.raises(EgressExchangeError):
        await svc.exchange(
            identity=SidecarIdentity(sandbox_id="sbx-1"),
            placeholder=placeholder,
            host="api.attacker.net",
        )
