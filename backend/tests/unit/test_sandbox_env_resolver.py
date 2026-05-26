# tests/unit/test_sandbox_env_resolver.py
from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.credentials.encryption import FernetBackend
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.sandbox_env import SandboxEnvRepository
from cubebox.services.credential import CredentialService
from cubebox.services.sandbox_env import SandboxEnvResolver, SandboxEnvService


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
async def seeded(session):
    org_id = "org-r"
    cred = CredentialService(
        CredentialRepository(session, org_id=org_id),
        FernetBackend([Fernet.generate_key()]),
        org_id=org_id,
        actor_user_id="u1",
    )
    svc = SandboxEnvService(
        repo=SandboxEnvRepository(session, org_id=org_id),
        credentials=cred,
        org_id=org_id,
        actor_user_id="u1",
    )
    # org-level GH token, then a user-level override of the same env name
    await svc.create_entry(
        env_name="GITHUB_TOKEN",
        is_secret=True,
        scope="org",
        workspace_id=None,
        user_id=None,
        hosts=["api.github.com"],
        header_names=None,
        secret_value="org-token",
        plain_value=None,
    )
    await svc.create_entry(
        env_name="GITHUB_TOKEN",
        is_secret=True,
        scope="user",
        workspace_id="ws-1",
        user_id="u1",
        hosts=["api.github.com"],
        header_names=None,
        secret_value="user-token",
        plain_value=None,
    )
    await svc.create_entry(
        env_name="LOG_LEVEL",
        is_secret=False,
        scope="org",
        workspace_id=None,
        user_id=None,
        hosts=None,
        header_names=None,
        secret_value=None,
        plain_value="info",
    )
    return SandboxEnvResolver(SandboxEnvRepository(session, org_id=org_id))


async def test_user_overrides_org(seeded):
    resolved = await seeded.resolve(workspace_id="ws-1", user_id="u1")
    by_name = {r.env_name: r for r in resolved}
    # GITHUB_TOKEN should resolve to the user-scope entry, LOG_LEVEL to org plain.
    assert by_name["GITHUB_TOKEN"].is_secret
    assert by_name["LOG_LEVEL"].plain_value == "info"
    assert len(resolved) == 2  # one effective entry per env_name


async def test_other_user_gets_org(seeded):
    resolved = await seeded.resolve(workspace_id="ws-1", user_id="u2")
    by_name = {r.env_name: r for r in resolved}
    assert "GITHUB_TOKEN" in by_name  # falls back to org-scope
