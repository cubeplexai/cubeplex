import io
import json as json_lib
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from langgraph.checkpoint.memory import MemorySaver
from PIL import Image
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import cubebox.db as _cubebox_db
from cubebox.api.app import create_app
from cubebox.api.middleware.rate_limit import limiter
from cubebox.auth.users import UserManager
from cubebox.config import config as _cubebox_config
from cubebox.db.engine import _build_database_url, engine
from cubebox.db.session import get_session
from cubebox.models import Role, User
from cubebox.repositories import (
    MembershipRepository,
    OrganizationRepository,
    WorkspaceRepository,
)
from cubebox.sandbox.local import LocalSandbox


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Force `@pytest.mark.e2e` on every test collected under `tests/e2e/`.

    Makes the directory the source of truth for the marker so a forgotten
    `pytestmark` can't slip past `-m "not e2e"` and run alongside unit
    tests. The directory-is-the-contract rule is mechanically enforced —
    see backend/CLAUDE.md.
    """
    e2e_dir = str(Path(__file__).parent.resolve())
    for item in items:
        if str(item.path).startswith(e2e_dir):
            item.add_marker(pytest.mark.e2e)


@pytest_asyncio.fixture(autouse=True)
async def _flush_test_redis(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """Delete this worktree's Redis keys before each e2e-marked test.

    Uses a prefix-scoped SCAN + DEL instead of FLUSHDB so two worktrees can
    run E2E in parallel against the same Redis without clobbering each other.
    The prefix is `{redis.key_prefix}:{env}` matching what app.py builds at
    startup; outside a worktree (CI) the key_prefix defaults to "cubebox" and
    env to "test", so behavior matches the previous FLUSHDB for a single
    isolated runner.
    """
    if request.node.get_closest_marker("e2e") is None:
        yield
        return
    client: Redis = Redis.from_url(
        _cubebox_config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=True,
    )
    try:
        # Delete only keys belonging to this worktree's prefix so parallel E2E
        # in other worktrees isn't clobbered. Prefix matches what app.py builds
        # at startup: f"{base_prefix}:{env}".
        base_prefix = _cubebox_config.get("redis.key_prefix", "cubebox")
        env_name = _cubebox_config.get("env", "test")
        pattern = f"{base_prefix}:{env_name}:*"
        deleted_keys: list[str] = []
        async for key in client.scan_iter(match=pattern, count=500):
            deleted_keys.append(key)
            if len(deleted_keys) >= 500:
                await client.delete(*deleted_keys)
                deleted_keys.clear()
        if deleted_keys:
            await client.delete(*deleted_keys)
    finally:
        await client.aclose()
    yield


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
    # Patch module-level async_session_maker so DefaultAuthProvider.authenticate
    # (which opens its own session inline) also uses NullPool in tests.
    _cubebox_db.async_session_maker = test_session_maker

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
    # Patch module-level async_session_maker so DefaultAuthProvider.authenticate
    # (which opens its own session inline) also uses NullPool in tests.
    _cubebox_db.async_session_maker = test_session_maker

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_maker() as session:
            yield session

    memory_saver = MemorySaver()
    app = create_app(
        checkpointer_factory=lambda: memory_saver,
        sandbox_factory=LocalSandbox,
    )
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
    # TestClient as a context manager runs the FastAPI lifespan — required
    # since auth routers are now mounted at lifespan startup (not in create_app).
    with TestClient(app) as sync_client:
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

    from cubebox.auth.users import _slugify_org_name

    org_name = f"Org {email}"
    org = await org_repo.create(name=org_name, slug=_slugify_org_name(org_name))
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


@pytest_asyncio.fixture
async def member_client_org_a() -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Fresh org A with a member user."""
    app, email, password, workspace_id = await _make_isolated_user(Role.MEMBER)
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, workspace_id


@pytest_asyncio.fixture
async def member_client_org_b() -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Fresh org B with a member user — distinct from org A."""
    app, email, password, workspace_id = await _make_isolated_user(Role.MEMBER)
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, workspace_id


async def _seed_skill_artifact(workspace_id: str, *, skill_md: bytes) -> tuple[str, str]:
    """Create Conversation + Artifact rows in DB and upload skill_md to object storage.

    Returns (artifact_id, conv_id).
    """
    from sqlalchemy import select as sa_select

    from cubebox.models import Artifact, Conversation, Membership, Workspace
    from cubebox.objectstore import get_objectstore_client

    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            ws = await session.get(Workspace, workspace_id)
            assert ws is not None
            org_id = ws.org_id

            stmt = sa_select(Membership).where(Membership.workspace_id == workspace_id)
            mem = (await session.execute(stmt)).scalars().first()
            assert mem is not None
            user_id = str(mem.user_id)

            conv = Conversation(
                org_id=org_id,
                workspace_id=workspace_id,
                creator_user_id=user_id,
                title="skill artifact test",
            )
            session.add(conv)
            await session.flush()

            artifact = Artifact(
                org_id=org_id,
                workspace_id=workspace_id,
                conversation_id=conv.id,
                name="my-test-skill",
                artifact_type="skill",
                path="/.skills/my-test-skill",
                entry_file="SKILL.md",
            )
            session.add(artifact)
            await session.flush()
            artifact_id = artifact.id
            conv_id = conv.id
            await session.commit()
    finally:
        await test_engine.dispose()

    store = get_objectstore_client()
    prefix = f"artifacts/{conv_id}/{artifact_id}/v1/"
    await store.upload_file(f"{prefix}SKILL.md", skill_md)
    return artifact_id, conv_id


_VALID_SKILL_MD = b"""\
---
name: my-test-skill
description: A test skill for artifact publish flow.
version: 0.1.0
keywords:
  - test
---

# My Test Skill

Use this skill in tests.
"""

_INVALID_SKILL_MD = b"""\
---
description: Missing required name field.
version: 0.1.0
---

# Bad Skill
"""


@pytest_asyncio.fixture
async def member_client_with_artifact() -> AsyncIterator[tuple[httpx.AsyncClient, str, str]]:
    """Fresh member + valid skill artifact in object storage.

    Yields (client, workspace_id, artifact_id).
    """
    app, email, password, workspace_id = await _make_isolated_user(Role.MEMBER)
    artifact_id, _ = await _seed_skill_artifact(workspace_id, skill_md=_VALID_SKILL_MD)
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, workspace_id, artifact_id


@pytest_asyncio.fixture
async def member_client_with_bad_artifact() -> AsyncIterator[tuple[httpx.AsyncClient, str, str]]:
    """Fresh member + skill artifact whose SKILL.md has invalid frontmatter.

    Yields (client, workspace_id, artifact_id).
    """
    app, email, password, workspace_id = await _make_isolated_user(Role.MEMBER)
    artifact_id, _ = await _seed_skill_artifact(workspace_id, skill_md=_INVALID_SKILL_MD)
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, workspace_id, artifact_id


@pytest.fixture(scope="session")
def docling_url() -> str:
    """Return a reachable DOCLING_URL or skip. Never mocks.

    M6 e2e tests must exercise a real docling-serve (CLAUDE.md: focus on E2E).
    Developers run ``docker compose up docling-serve`` locally; CI provides the
    URL via the DOCLING_URL secret.
    """
    import os

    url = os.environ.get("DOCLING_URL")
    if not url:
        pytest.skip("DOCLING_URL not set — external docling-serve required for e2e")
    try:
        resp = httpx.get(f"{url.rstrip('/')}/health", timeout=5)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — any probe failure → skip
        pytest.skip(f"docling-serve at {url} unreachable: {exc}")
    return url


async def collect_sse_events(
    client: httpx.AsyncClient,
    url: str,
    json_data: dict,  # type: ignore[type-arg]
) -> list[dict]:  # type: ignore[type-arg]
    """POST to an SSE endpoint and collect all parsed events."""
    events = []
    async with client.stream(
        "POST",
        url,
        json=json_data,
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    ) as response:
        assert response.status_code == 200, response.text
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json_lib.loads(line[6:]))
    return events


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Yield a raw AsyncSession connected to the test database (NullPool).

    Use for repository-layer E2E tests that verify DB state directly without
    going through the HTTP layer.
    """
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            yield session
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    """Yield an async Redis client connected to the test Redis instance.

    Use for repository/seeder-layer E2E tests that interact with Redis directly.
    """
    client: Redis = Redis.from_url(
        _cubebox_config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=False,
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def sample_png_bytes() -> bytes:
    """Tiny valid PNG, generated in-memory."""
    img = Image.new("RGB", (100, 100), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Minimal valid PDF (one empty page)."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f\n0000000009 00000 n\n"
        b"0000000052 00000 n\n0000000095 00000 n\n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n145\n%%EOF\n"
    )
