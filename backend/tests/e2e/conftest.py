from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.api.app import create_app
from cubebox.db.engine import _build_database_url
from cubebox.db.session import get_session


@pytest.fixture
def client() -> TestClient:
    """Create test client for API testing with test config.

    Overrides the get_session dependency with a NullPool engine to avoid
    cross-event-loop connection reuse issues between test functions.
    """
    url = _build_database_url()
    test_engine = create_async_engine(url, poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)
