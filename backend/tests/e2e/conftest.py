import json as json_lib
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.api.app import create_app
from cubebox.api.middleware.rate_limit import limiter
from cubebox.auth.users import UserManager
from cubebox.db.engine import _build_database_url, engine
from cubebox.db.session import get_session
from cubebox.models import Role, User
from cubebox.repositories import (
    MembershipRepository,
    OrganizationRepository,
    WorkspaceRepository,
)
from cubebox.sandbox.local import LocalSandbox


@pytest.fixture(autouse=True)
def _reset_rate_limiter_between_tests() -> Iterator[None]:
    """Reset the shared slowapi limiter between tests.

    Every fixture client logs in at setup, so register/login limits
    (3/min, 5/min) otherwise accumulate across tests — all requests share the
    same ASGI-transport remote address and trip 429 after a few tests.
    """
    limiter.reset()
    yield
    limiter.reset()


DEFAULT_ORG_ID = "default-org"
DEFAULT_WS_ID = "default-ws"
DEFAULT_TEST_EMAIL = "test-default@example.com"
DEFAULT_TEST_PASSWORD = "test-default-password-12345"


@asynccontextmanager
async def _lifespan_context(app: FastAPI) -> AsyncIterator[None]:
    """Manually invoke FastAPI lifespan startup and shutdown.

    This is needed because httpx.ASGITransport doesn't automatically
    manage the ASGI lifespan protocol.
    """
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        yield


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

    memory_saver = MemorySaver()
    app = create_app(checkpointer_factory=lambda: memory_saver, sandbox_factory=LocalSandbox)
    app.dependency_overrides[get_session] = override_get_session
    return app


async def _ensure_default_user_and_membership() -> None:
    """Idempotently ensure a DEFAULT_TEST_EMAIL user exists as admin of default-ws."""
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with test_session_maker() as session:
            user_db = SQLAlchemyUserDatabase(session, User)
            existing = await user_db.get_by_email(DEFAULT_TEST_EMAIL)
            if existing is None:
                manager = UserManager(user_db)
                user = await manager.create(
                    BaseUserCreate(email=DEFAULT_TEST_EMAIL, password=DEFAULT_TEST_PASSWORD),
                    safe=False,
                )
            else:
                user = existing

            mem_repo = MembershipRepository(session)
            role = await mem_repo.get_role(user_id=user.id, workspace_id=DEFAULT_WS_ID)
            if role is None:
                await mem_repo.grant(user_id=user.id, workspace_id=DEFAULT_WS_ID, role=Role.ADMIN)
    finally:
        await test_engine.dispose()


async def _login_and_attach(client: httpx.AsyncClient, email: str, password: str) -> None:
    """Log in and set the CSRF header on the client."""
    await client.get("/api/v1/auth/me")  # obtain CSRF cookie (401 but sets cookie)
    csrf = client.cookies.get("cubebox_csrf") or ""
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"
    client.headers["X-CSRF-Token"] = client.cookies.get("cubebox_csrf") or csrf


@pytest_asyncio.fixture
async def client() -> AsyncIterator[TestClient]:
    """Sync test client, auto-logged-in as default user in default-ws.

    Business-scoped calls should be prefixed with `/api/v1/ws/{DEFAULT_WS_ID}/...`.
    """
    await _ensure_default_user_and_membership()
    app = _make_test_app()
    sync_client = TestClient(app)
    sync_client.get("/api/v1/auth/me")  # obtain CSRF cookie
    csrf = sync_client.cookies.get("cubebox_csrf") or ""
    r = sync_client.post(
        "/api/v1/auth/login",
        data={"username": DEFAULT_TEST_EMAIL, "password": DEFAULT_TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"
    sync_client.headers["X-CSRF-Token"] = sync_client.cookies.get("cubebox_csrf") or csrf
    yield sync_client


@pytest_asyncio.fixture
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    """Async HTTP client, auto-logged-in as default user in default-ws.

    Business-scoped calls should be prefixed with `/api/v1/ws/{DEFAULT_WS_ID}/...`.
    """
    await _ensure_default_user_and_membership()
    app = _make_test_app()
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, DEFAULT_TEST_EMAIL, DEFAULT_TEST_PASSWORD)
            yield c
    await engine.dispose()


@pytest_asyncio.fixture
async def memory_client() -> AsyncIterator[httpx.AsyncClient]:
    """Async client using MemorySaver + LocalSandbox, auto-logged-in."""
    await _ensure_default_user_and_membership()
    app = _make_memory_test_app()
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, DEFAULT_TEST_EMAIL, DEFAULT_TEST_PASSWORD)
            yield c
    await engine.dispose()


@pytest_asyncio.fixture
async def unauthenticated_memory_client() -> AsyncIterator[httpx.AsyncClient]:
    """Async client with no login — for negative auth tests."""
    await _ensure_default_user_and_membership()
    app = _make_memory_test_app()
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    await engine.dispose()


async def _ensure_test_user_membership(
    session: AsyncSession, *, email: str, role: Role
) -> tuple[User, str, str]:
    """Create a user + org + workspace + membership; return (user, workspace_id, password)."""
    org_repo = OrganizationRepository(session)
    ws_repo = WorkspaceRepository(session)
    mem_repo = MembershipRepository(session)

    org = await org_repo.create(name=f"Org {email}")
    ws = await ws_repo.create(org_id=org.id, name=f"WS {email}")

    password = secrets.token_urlsafe(16)
    user_db = SQLAlchemyUserDatabase(session, User)
    manager = UserManager(user_db)
    user = await manager.create(BaseUserCreate(email=email, password=password), safe=False)
    await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=role)
    return user, ws.id, password


async def _make_isolated_user(role: Role) -> tuple[FastAPI, str, str, str]:
    """Build a fresh app + seed a brand-new user+ws with given role.

    Returns (app, email, password, workspace_id).
    """
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with test_session_maker() as session:
            email = f"{role.value}-{secrets.token_hex(4)}@example.com"
            _, workspace_id, password = await _ensure_test_user_membership(
                session, email=email, role=role
            )
    finally:
        await test_engine.dispose()

    app = _make_memory_test_app()
    return app, email, password, workspace_id


@pytest_asyncio.fixture
async def authenticated_client() -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Fresh client logged in as a brand-new admin of a brand-new workspace.

    Yields ``(client, workspace_id)``. Callers prepend ``/api/v1/ws/{workspace_id}``
    to business-scoped paths.
    """
    app, email, password, workspace_id = await _make_isolated_user(Role.ADMIN)
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, workspace_id


@pytest_asyncio.fixture
async def admin_client(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> tuple[httpx.AsyncClient, str]:
    """Alias — authenticated_client is already admin."""
    return authenticated_client


@pytest_asyncio.fixture
async def member_client() -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Fresh client logged in as a brand-new member (not admin) of a brand-new workspace."""
    app, email, password, workspace_id = await _make_isolated_user(Role.MEMBER)
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, workspace_id


async def collect_sse_events(
    client: httpx.AsyncClient,
    url: str,
    json_data: dict,  # type: ignore[type-arg]
) -> list[dict]:  # type: ignore[type-arg]
    """POST to an SSE endpoint and collect all parsed events."""
    events = []
    async with client.stream("POST", url, json=json_data) as response:
        assert response.status_code == 200, response.text
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json_lib.loads(line[6:]))
    return events
