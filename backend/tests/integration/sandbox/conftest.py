"""Fixtures for SandboxManager integration tests.

These tests run against the real test Postgres DB (so the partial unique
indexes from the topic_id migration are actually exercised) but monkey-
patch ``opensandbox.Sandbox.create`` / ``.connect`` so no provider is
needed. Modeled on ``tests/e2e/conftest.py``'s
``fake_opensandbox`` + ``seeded_org_ws_user`` pair.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.auth.users import UserManager, _slugify_org_name
from cubebox.credentials.encryption import FernetBackend
from cubebox.db.engine import _build_database_url
from cubebox.models import (
    Membership as MembershipModel,
)
from cubebox.models import (
    OrganizationMembership,
    OrgRole,
    Role,
    User,
)
from cubebox.repositories import (
    MembershipRepository,
    OrganizationMembershipRepository,
    OrganizationRepository,
    WorkspaceRepository,
)
from cubebox.repositories.topic import TopicRepository
from cubebox.sandbox.manager import SandboxManager


class _FakeRaw:
    """Minimal stand-in for ``opensandbox.Sandbox`` (no provider needed).

    ``connect(sandbox_id=...)`` echoes the requested id so reuse paths can
    assert stable identity across multiple ``get_or_create`` calls.
    """

    def __init__(self, sandbox_id: str | None = None) -> None:
        self.id = sandbox_id or f"prov-{secrets.token_hex(6)}"

    async def is_healthy(self) -> bool:
        return True

    async def close(self) -> None:  # pragma: no cover - trivial
        return None

    async def kill(self) -> None:  # pragma: no cover - trivial
        return None


@pytest.fixture
def fake_opensandbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Patch opensandbox.Sandbox.create / connect with ``_FakeRaw`` fakes."""
    import opensandbox

    async def _fake_create(image: str, **kwargs: Any) -> _FakeRaw:
        del image, kwargs
        return _FakeRaw()

    async def _fake_connect(sandbox_id: str, **kwargs: Any) -> _FakeRaw:
        del kwargs
        # Echo the requested sandbox_id so reuse paths return a handle
        # whose ``id`` matches the DB row's ``sandbox_id`` — otherwise
        # ``OpenSandbox.id`` flips on every reconnect and reuse looks
        # like a fresh create.
        return _FakeRaw(sandbox_id=sandbox_id)

    monkeypatch.setattr(opensandbox.Sandbox, "create", staticmethod(_fake_create))
    monkeypatch.setattr(opensandbox.Sandbox, "connect", staticmethod(_fake_connect))
    yield


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yields async_sessionmaker pointing at the test Postgres DB."""
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def topic_scope_fixture() -> AsyncIterator[tuple[str, str, str, str]]:
    """One org, one workspace, one user, one topic. The topic is created
    with the user as owner so subsequent participant-scoped reads work.

    Yields ``(org_id, workspace_id, user_id, topic_id)``.
    """
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with test_session_maker() as session:
            email = f"sbx-topic-{secrets.token_hex(4)}@example.com"
            org_name = f"Org {email}"
            org = await OrganizationRepository(session).create(
                name=org_name, slug=_slugify_org_name(org_name)
            )
            ws = await WorkspaceRepository(session).create(org_id=org.id, name=f"WS {email}")

            password = secrets.token_urlsafe(16)
            user_db = SQLAlchemyUserDatabase(session, User)
            manager = UserManager(user_db)
            user = await manager.create(BaseUserCreate(email=email, password=password), safe=False)

            # Wipe bootstrap personal org / memberships so the only scope is ours.
            await session.execute(
                delete(OrganizationMembership).where(
                    OrganizationMembership.user_id == user.id,  # type: ignore[arg-type]
                    OrganizationMembership.org_id != org.id,  # type: ignore[arg-type]
                )
            )
            await session.execute(
                delete(MembershipModel).where(
                    MembershipModel.user_id == user.id,  # type: ignore[arg-type]
                    MembershipModel.workspace_id != ws.id,  # type: ignore[arg-type]
                )
            )
            await session.commit()

            mem_repo = MembershipRepository(session)
            await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
            om_repo = OrganizationMembershipRepository(session)
            existing_om = await om_repo.get_role(user_id=user.id, org_id=org.id)
            if existing_om is None:
                await om_repo.grant(user_id=user.id, org_id=org.id, role=OrgRole.OWNER)
            await session.commit()

            topic_repo = TopicRepository(
                session,
                org_id=org.id,
                workspace_id=ws.id,
                user_id=str(user.id),
            )
            topic = await topic_repo.create_topic(
                title="Integration Topic",
                sandbox_mode="dedicated",
            )
            await session.commit()

            yield (org.id, ws.id, str(user.id), topic.id)
    finally:
        await test_engine.dispose()


@pytest.fixture
def sandbox_manager(
    session_factory: async_sessionmaker[AsyncSession],
) -> SandboxManager:
    """A SandboxManager wired with a throw-away Fernet backend."""
    return SandboxManager(session_factory, FernetBackend([Fernet.generate_key()]))
