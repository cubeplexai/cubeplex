"""SandboxManager-level E2E test for polymorphic-scope sandbox routing.

Repo unit tests alone don't catch the bugs where ``LazySandbox`` /
``SandboxManager`` forget to thread ``(scope_type, scope_id)`` through to
the lookup / reserve callsites. This file exercises the manager
end-to-end against the real test Postgres DB (so the partial unique index
installed by the polymorphic-scope migration actually fires) with the
OpenSandbox provider faked out.

Lives in ``tests/e2e/`` because the fixtures here open ``AsyncSession``s,
run repositories, and rely on the per-slot worktree test DB — exactly the
shape of an e2e test per the project's test-layout rule.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.auth.users import UserManager, _slugify_org_name
from cubeplex.credentials.encryption import FernetBackend
from cubeplex.db.engine import _build_database_url
from cubeplex.models import (
    Membership as MembershipModel,
)
from cubeplex.models import (
    OrganizationMembership,
    OrgRole,
    Role,
    User,
)
from cubeplex.repositories import (
    MembershipRepository,
    OrganizationMembershipRepository,
    OrganizationRepository,
    WorkspaceRepository,
)
from cubeplex.repositories.topic import TopicRepository
from cubeplex.sandbox.manager import SandboxManager


class _FakeRaw:
    """Minimal stand-in for ``opensandbox.Sandbox`` (no provider needed).

    ``connect(sandbox_id=...)`` echoes the requested id so reuse paths can
    assert stable identity across multiple ``get_or_create`` calls.
    """

    def __init__(self, sandbox_id: str | None = None) -> None:
        self.id = sandbox_id or f"prov-{secrets.token_hex(6)}"

    async def is_healthy(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    async def kill(self) -> None:
        return None


@pytest.fixture
def fake_opensandbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Patch opensandbox.Sandbox.create / connect with echoing fakes.

    Overrides the broader ``fake_opensandbox`` defined in ``tests/e2e/conftest.py``
    so reuse-path assertions (``again.id == topic_sb.id``) see a stable id.
    """
    import opensandbox

    async def _fake_create(image: str, **kwargs: Any) -> _FakeRaw:
        del image, kwargs
        return _FakeRaw()

    async def _fake_connect(sandbox_id: str, **kwargs: Any) -> _FakeRaw:
        del kwargs
        return _FakeRaw(sandbox_id=sandbox_id)

    monkeypatch.setattr(opensandbox.Sandbox, "create", staticmethod(_fake_create))
    monkeypatch.setattr(opensandbox.Sandbox, "connect", staticmethod(_fake_connect))
    yield


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


async def test_dedicated_topic_sandbox_isolated_from_personal(
    fake_opensandbox: None,
    sandbox_manager: SandboxManager,
    session_factory: async_sessionmaker[Any],
    topic_scope_fixture: tuple[str, str, str, str],
) -> None:
    """A user's personal sandbox and their dedicated topic sandbox are
    distinct provider instances and survive a second lookup."""
    del fake_opensandbox
    org_id, ws_id, user_id, topic_id = topic_scope_fixture

    personal = await sandbox_manager.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )
    topic_sb = await sandbox_manager.get_or_create(
        scope_type="topic",
        scope_id=topic_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )
    assert personal.id != topic_sb.id

    again = await sandbox_manager.get_or_create(
        scope_type="topic",
        scope_id=topic_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )
    assert again.id == topic_sb.id

    again_personal = await sandbox_manager.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )
    assert again_personal.id == personal.id

    async with session_factory() as s:
        rows = (
            await s.execute(
                sa.text(
                    "SELECT scope_type, scope_id, sandbox_id FROM user_sandboxes "
                    "WHERE user_id=:u AND workspace_id=:w "
                    "AND status IN ('provisioning','running')"
                ),
                {"u": user_id, "w": ws_id},
            )
        ).all()
    scope_keys = {(r[0], r[1]) for r in rows}
    sandbox_ids = {r[2] for r in rows}
    assert scope_keys == {("user", user_id), ("topic", topic_id)}
    assert len(sandbox_ids) == 2


async def test_topic_sandbox_shared_across_participants(
    fake_opensandbox: None,
    sandbox_manager: SandboxManager,
    session_factory: async_sessionmaker[Any],
    topic_scope_fixture: tuple[str, str, str, str],
) -> None:
    """A second participant joining the same topic attaches to the existing
    topic sandbox rather than creating a duplicate."""
    del fake_opensandbox
    org_id, ws_id, user_id, topic_id = topic_scope_fixture

    winner = await sandbox_manager.get_or_create(
        scope_type="topic",
        scope_id=topic_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )

    other_user = "user-other-participant"
    shared = await sandbox_manager.get_or_create(
        scope_type="topic",
        scope_id=topic_id,
        user_id=other_user,
        org_id=org_id,
        workspace_id=ws_id,
    )
    assert shared.id == winner.id

    async with session_factory() as s:
        count = (
            await s.execute(
                sa.text(
                    "SELECT COUNT(*) FROM user_sandboxes "
                    "WHERE workspace_id=:w AND scope_type='topic' AND scope_id=:t "
                    "AND status IN ('provisioning','running')"
                ),
                {"w": ws_id, "t": topic_id},
            )
        ).scalar_one()
    assert count == 1


async def test_standalone_group_chat_scope_distinct_from_user(
    fake_opensandbox: None,
    sandbox_manager: SandboxManager,
    session_factory: async_sessionmaker[Any],
    topic_scope_fixture: tuple[str, str, str, str],
) -> None:
    """A standalone group chat (``scope_type='conversation'``) is keyed
    separately from any user-scoped sandbox the same caller already has."""
    del fake_opensandbox
    org_id, ws_id, user_id, _topic_id = topic_scope_fixture

    personal = await sandbox_manager.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )
    conv_id = "conv-group-1"
    group = await sandbox_manager.get_or_create(
        scope_type="conversation",
        scope_id=conv_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )
    assert personal.id != group.id

    async with session_factory() as s:
        rows = (
            await s.execute(
                sa.text(
                    "SELECT scope_type, scope_id FROM user_sandboxes "
                    "WHERE user_id=:u AND workspace_id=:w "
                    "AND status IN ('provisioning','running')"
                ),
                {"u": user_id, "w": ws_id},
            )
        ).all()
    assert {(r[0], r[1]) for r in rows} == {
        ("user", user_id),
        ("conversation", conv_id),
    }
