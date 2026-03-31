import json as json_lib
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.api.app import create_app
from cubebox.db.engine import _build_database_url
from cubebox.db.session import get_session
from cubebox.sandbox.local import LocalSandbox


def _make_test_app() -> FastAPI:
    """Create a FastAPI app with NullPool engine for test isolation."""
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
    return app


def _make_memory_test_app() -> FastAPI:
    """Create a test app using MemorySaver and LocalSandbox (no DB needed for agent)."""
    url = _build_database_url()
    test_engine = create_async_engine(url, poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_maker() as session:
            yield session

    # Use in-memory checkpointer and local sandbox for agent tests
    memory_saver = MemorySaver()
    app = create_app(
        checkpointer_factory=lambda: memory_saver,
        sandbox_factory=LocalSandbox,
    )
    app.dependency_overrides[get_session] = override_get_session
    return app


@pytest.fixture
def client() -> TestClient:
    """Create sync test client."""
    return TestClient(_make_test_app())


@pytest_asyncio.fixture
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    """Create async HTTP client for testing streaming endpoints."""
    transport = httpx.ASGITransport(app=_make_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def memory_client() -> AsyncIterator[httpx.AsyncClient]:
    """Async client using MemorySaver + LocalSandbox (for agent streaming tests)."""
    transport = httpx.ASGITransport(app=_make_memory_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def collect_sse_events(
    client: httpx.AsyncClient,
    url: str,
    json_data: dict,  # type: ignore[type-arg]
) -> list[dict]:  # type: ignore[type-arg]
    """POST to an SSE endpoint and collect all parsed events."""
    events = []
    async with client.stream("POST", url, json=json_data) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json_lib.loads(line[6:]))
    return events
