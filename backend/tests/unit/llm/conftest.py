"""Local fixtures for cubeplex.llm unit tests — in-memory SQLite + encryption backend."""

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubeplex.credentials.encryption import FernetBackend


@pytest_asyncio.fixture
async def async_session():
    """In-memory SQLite session for fast unit tests with full schema."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()


@pytest.fixture
def encryption_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])
