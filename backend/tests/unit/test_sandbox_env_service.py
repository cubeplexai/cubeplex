from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.models import Credential
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.sandbox_env.host_rules import HostPatternError
from cubeplex.services.credential import CredentialService
from cubeplex.services.sandbox_env import (
    SandboxEnvConflictError,
    SandboxEnvService,
    SandboxEnvShapeError,
)


# Self-contained in-memory session — the unit-test convention used by
# tests/unit/test_credential_service.py. (`db_session` is defined only in
# tests/e2e/conftest.py and is NOT visible to tests/unit.)
@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def service(session):
    org_id = "org-test"
    cred_svc = CredentialService(
        CredentialRepository(session, org_id=org_id),
        FernetBackend([Fernet.generate_key()]),
        org_id=org_id,
        actor_user_id="user-1",
    )
    return SandboxEnvService(
        repo=SandboxEnvRepository(session, org_id=org_id),
        credentials=cred_svc,
        org_id=org_id,
        actor_user_id="user-1",
    )


async def test_create_secret_entry(service):
    entry_id = await service.create_entry(
        env_name="GITHUB_TOKEN",
        is_secret=True,
        scope="workspace",
        workspace_id="ws-1",
        user_id=None,
        hosts=["api.github.com"],
        header_names=None,
        secret_value="ghp_xxx",
    )
    assert entry_id.startswith("senv-")


async def test_create_plain_entry(service):
    entry_id = await service.create_entry(
        env_name="LOG_LEVEL",
        is_secret=False,
        scope="org",
        workspace_id=None,
        user_id=None,
        hosts=None,
        header_names=None,
        secret_value="debug",
    )
    assert entry_id.startswith("senv-")


async def test_secret_requires_hosts(service):
    with pytest.raises(SandboxEnvShapeError):
        await service.create_entry(
            env_name="X",
            is_secret=True,
            scope="org",
            workspace_id=None,
            user_id=None,
            hosts=None,
            header_names=None,
            secret_value="v",
        )


async def test_bad_scope_shape(service):
    with pytest.raises(SandboxEnvShapeError):
        await service.create_entry(
            env_name="X",
            is_secret=False,
            scope="workspace",
            workspace_id=None,
            user_id=None,
            hosts=None,
            header_names=None,
            secret_value="v",
        )


async def test_bad_host_rejected(service):
    with pytest.raises(HostPatternError):
        await service.create_entry(
            env_name="X",
            is_secret=True,
            scope="org",
            workspace_id=None,
            user_id=None,
            hosts=["*.com"],
            header_names=None,
            secret_value="v",
        )


# ---------------------------------------------------------------------------
# Fix A tests: preflight conflict → SandboxEnvConflictError + no orphan cred
# ---------------------------------------------------------------------------


@pytest.fixture
async def service_with_session(session: AsyncSession):
    """Return (service, session) so tests can inspect DB state directly."""
    org_id = "org-test"
    cred_svc = CredentialService(
        CredentialRepository(session, org_id=org_id),
        FernetBackend([Fernet.generate_key()]),
        org_id=org_id,
        actor_user_id="user-1",
    )
    svc = SandboxEnvService(
        repo=SandboxEnvRepository(session, org_id=org_id),
        credentials=cred_svc,
        org_id=org_id,
        actor_user_id="user-1",
    )
    return svc, session


async def _credential_count(session: AsyncSession) -> int:
    result = await session.execute(select(Credential))
    return len(result.scalars().all())


async def test_duplicate_env_name_raises_conflict(service_with_session):
    """Creating a secret with a colliding env_name raises SandboxEnvConflictError."""
    svc, session = service_with_session
    await svc.create_entry(
        env_name="GITHUB_TOKEN",
        is_secret=True,
        scope="workspace",
        workspace_id="ws-1",
        user_id=None,
        hosts=["api.github.com"],
        header_names=None,
        secret_value="ghp_first",
    )
    with pytest.raises(SandboxEnvConflictError):
        await svc.create_entry(
            env_name="GITHUB_TOKEN",
            is_secret=True,
            scope="workspace",
            workspace_id="ws-1",
            user_id=None,
            hosts=["api.github.com"],
            header_names=None,
            secret_value="ghp_second",
        )


async def test_conflict_leaves_no_orphan_credential(service_with_session):
    """On conflict, no extra credential row is left behind."""
    svc, session = service_with_session
    await svc.create_entry(
        env_name="UNIQUE_TOKEN",
        is_secret=True,
        scope="org",
        workspace_id=None,
        user_id=None,
        hosts=["api.example.com"],
        header_names=None,
        secret_value="original",
    )
    cred_count_before = await _credential_count(session)

    with pytest.raises(SandboxEnvConflictError):
        await svc.create_entry(
            env_name="UNIQUE_TOKEN",
            is_secret=True,
            scope="org",
            workspace_id=None,
            user_id=None,
            hosts=["api.example.com"],
            header_names=None,
            secret_value="duplicate",
        )

    cred_count_after = await _credential_count(session)
    assert cred_count_after == cred_count_before, (
        f"Expected no new credential rows, but count went from {cred_count_before} "
        f"to {cred_count_after}"
    )


async def test_conflict_error_is_subclass_of_shape_error(service_with_session):
    """SandboxEnvConflictError must be a subclass of SandboxEnvShapeError for route compat."""
    assert issubclass(SandboxEnvConflictError, SandboxEnvShapeError)
