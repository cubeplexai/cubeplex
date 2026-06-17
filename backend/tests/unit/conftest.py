from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import cubebox.models  # noqa: F401  -- register all models on metadata
from cubebox.auth.users import UserManager
from cubebox.credentials.encryption import FernetBackend
from cubebox.models import (
    Organization,
    OrganizationMembership,
    OrgRole,
    User,
)
from cubebox.repositories import OrganizationRepository


@pytest.fixture
def mock_encryption_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


@pytest_asyncio.fixture
async def sso_session() -> AsyncIterator[AsyncSession]:
    """In-memory SQLite session for SSO identity-resolution unit tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def sso_user_manager(sso_session: AsyncSession) -> AsyncIterator[UserManager]:
    """Real fastapi-users UserManager backed by the in-memory SQLite session.

    on_after_register fires the full multi_tenant bootstrap (org + workspace
    + memberships + agent config). MCP enrollment and skill installation see
    an empty DB and no-op cleanly; email send is best-effort and swallowed.
    """
    user_db: SQLAlchemyUserDatabase[User, str] = SQLAlchemyUserDatabase(sso_session, User)
    yield UserManager(user_db)


@pytest_asyncio.fixture
async def make_org_with_user(
    sso_session: AsyncSession,
) -> Callable[..., Awaitable[tuple[Organization, User]]]:
    """Build an Organization + User + OrganizationMembership without going
    through UserManager (no auto-bootstrap)."""

    async def _make(*, email: str = "u@example.com") -> tuple[Organization, User]:
        org = await OrganizationRepository(sso_session).create(
            name=f"Org {email}", slug=email.replace("@", "-").replace(".", "-")[:30]
        )
        user = User(
            email=email,
            hashed_password="not-a-real-hash",
            display_name=email.split("@", 1)[0],
            is_active=True,
            is_verified=True,
        )
        sso_session.add(user)
        await sso_session.flush()
        sso_session.add(OrganizationMembership(user_id=user.id, org_id=org.id, role=OrgRole.MEMBER))
        await sso_session.commit()
        return org, user

    return _make
