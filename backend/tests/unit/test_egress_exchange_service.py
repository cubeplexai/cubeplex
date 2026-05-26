from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

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


async def _seed_with_expiry(
    session: AsyncSession, expires_at: datetime
) -> tuple[EgressExchangeService, str]:
    """Seed a ref with an explicit expires_at (tz-naive, as Postgres stores it)."""
    backend = FernetBackend([Fernet.generate_key()])
    cred = CredentialService(
        CredentialRepository(session, org_id="org-exp"),
        backend,
        org_id="org-exp",
        actor_user_id="u-exp",
    )
    cred_id = await cred.create(kind=SANDBOX_ENV_KIND, name="t-exp", plaintext="tok_expiry")
    placeholder = mint_placeholder()
    # Store expires_at as tz-naive to simulate what Postgres DateTime() returns
    naive_expires_at = expires_at.replace(tzinfo=None)
    await EgressRefRepository(session).add(
        EgressRef(
            ref_hash=hash_placeholder(placeholder),
            sandbox_id="sbx-exp",
            org_id="org-exp",
            workspace_id="ws-exp",
            user_id="u-exp",
            run_id=None,
            bindings=[
                {
                    "ref_hash": hash_placeholder(placeholder),
                    "env_name": "API_TOKEN",
                    "hosts": ["api.example.com"],
                    "header_names": None,
                    "credential_id": cred_id,
                }
            ],
            expires_at=naive_expires_at,
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


async def test_exchange_succeeds_with_future_expiry(session: AsyncSession) -> None:
    """A ref whose expires_at is in the future (tz-naive from DB) must succeed."""
    future = datetime.now(UTC) + timedelta(hours=1)
    svc, placeholder = await _seed_with_expiry(session, future)
    secret = await svc.exchange(
        identity=SidecarIdentity(sandbox_id="sbx-exp"),
        placeholder=placeholder,
        host="api.example.com",
    )
    assert secret == "tok_expiry"


async def test_exchange_fails_with_past_expiry(session: AsyncSession) -> None:
    """A ref whose expires_at is in the past (tz-naive from DB) must be rejected."""
    past = datetime.now(UTC) - timedelta(hours=1)
    svc, placeholder = await _seed_with_expiry(session, past)
    with pytest.raises(EgressExchangeError):
        await svc.exchange(
            identity=SidecarIdentity(sandbox_id="sbx-exp"),
            placeholder=placeholder,
            host="api.example.com",
        )


async def test_stale_credential_fails_closed(session: AsyncSession) -> None:
    """If the bound credential was deleted while a ref is still valid, the
    exchange must raise EgressExchangeError (→ 403), not a raw CredentialNotFound
    (→ 500)."""
    backend = FernetBackend([Fernet.generate_key()])
    cred = CredentialService(
        CredentialRepository(session, org_id="org-stale"),
        backend,
        org_id="org-stale",
        actor_user_id="u1",
    )
    cred_id = await cred.create(kind=SANDBOX_ENV_KIND, name="gone", plaintext="secret")
    placeholder = mint_placeholder()
    await EgressRefRepository(session).add(
        EgressRef(
            ref_hash=hash_placeholder(placeholder),
            sandbox_id="sbx-stale",
            org_id="org-stale",
            workspace_id="ws-stale",
            user_id="u1",
            run_id=None,
            bindings=[
                {
                    "ref_hash": hash_placeholder(placeholder),
                    "env_name": "API_TOKEN",
                    "hosts": ["api.example.com"],
                    "header_names": None,
                    "credential_id": cred_id,
                }
            ],
        )
    )
    # Delete the credential out from under the still-valid ref.
    await CredentialRepository(session, org_id="org-stale").delete(cred_id)

    svc = EgressExchangeService(
        ref_repo=EgressRefRepository(session),
        credentials_factory=lambda org_id: CredentialService(
            CredentialRepository(session, org_id=org_id),
            backend,
            org_id=org_id,
            actor_user_id=None,
        ),
    )
    with pytest.raises(EgressExchangeError):
        await svc.exchange(
            identity=SidecarIdentity(sandbox_id="sbx-stale"),
            placeholder=placeholder,
            host="api.example.com",
        )
