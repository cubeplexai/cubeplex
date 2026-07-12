import asyncio
import contextlib
import io
import json as json_lib
import os
import secrets
import socket
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
import uvicorn
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from PIL import Image
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import cubeplex.db as _cubeplex_db
from cubeplex.api.app import create_app
from cubeplex.api.middleware.rate_limit import limiter
from cubeplex.auth.users import UserManager
from cubeplex.config import config as _cubeplex_config
from cubeplex.credentials.encryption import FernetBackend
from cubeplex.db.engine import _build_database_url, engine
from cubeplex.db.session import get_session
from cubeplex.models import Role, User
from cubeplex.repositories import (
    MembershipRepository,
    OrganizationRepository,
    WorkspaceRepository,
)
from cubeplex.sandbox.base import ExecuteResult, Sandbox
from cubeplex.sandbox.lazy import LazySandbox
from cubeplex.sandbox.manager import SandboxManager
from cubeplex.skills.sandbox_paths import SKILLS_ROOT
from cubeplex.skills.sync_tar import SKILLS_DELTA_TGZ_PATH
from tests.e2e.helpers import csrf_cookie_name


def _auth_cookie_name() -> str:
    """Resolved auth cookie name; honours per-worktree env override."""
    return str(_cubeplex_config.get("auth.cookie_name", "cubeplex_auth"))


# ---------------------------------------------------------------------------
# MemSandbox: shared in-memory sandbox for skills sync tests
# ---------------------------------------------------------------------------


class MemSandbox(Sandbox):
    """Minimal in-memory Sandbox for testing ``_sync_skills``.

    ``upload`` stores bytes by path; ``download`` reads them back.
    ``execute`` processes the ``&&``-separated shell command chain that
    ``_sync_skills`` emits — in the SAME ORDER as the real shell:
    mkdir-p → rm-rf (wipe old) → tar-xzf (extract) → rm-f (drop tgz).
    Unrecognised tokens (mkdir, echo, …) are accepted silently.
    ``rm -rf <dir>`` removes all keys equal to or prefixed by ``<dir>/``
    so uninstall tests can assert the directory is gone.
    """

    _SKILLS_TGZ = SKILLS_DELTA_TGZ_PATH

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    @property
    def id(self) -> str:
        return "mem-sandbox"

    @property
    def workdir(self) -> str:
        return "/workspace"

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
    ) -> ExecuteResult:
        del timeout, envs
        # Process each ``&&``-separated token in ORDER so that:
        #   mkdir -p ... → rm -rf <old> → tar -xzf ... → rm -f tgz
        # mirrors what the real sandbox shell does.  Extracting the tar first
        # then iterating rm-rf tokens would wipe the just-extracted files.
        import tarfile

        for token in command.split("&&"):
            stripped = token.strip()

            # tar -xzf /tmp/skills_delta.tgz -C /workspace/.skills
            if "tar -xzf" in stripped and self._SKILLS_TGZ in stripped:
                tgz = self._files.get(self._SKILLS_TGZ)
                if tgz:
                    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tf:
                        for member in tf.getmembers():
                            if member.isfile():
                                f = tf.extractfile(member)
                                if f is not None:
                                    dest = f"{SKILLS_ROOT}/{member.name}"
                                    self._files[dest] = f.read()

            # rm -rf / rm -f: drop matching key(s).
            # ``rm -rf /workspace/.skills/probe-1`` is a directory removal —
            # remove all keys equal to the path OR starting with ``path/``.
            elif stripped.startswith("rm -rf ") or stripped.startswith("rm -f "):
                path = stripped.split(None, 2)[-1].strip("'\"")
                prefix = path.rstrip("/") + "/"
                to_drop = [k for k in self._files if k == path or k.startswith(prefix)]
                for k in to_drop:
                    self._files.pop(k, None)

            # mkdir -p and other commands: benign no-ops in the in-memory FS.

        return ExecuteResult(output="", exit_code=0)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        for path, content in files:
            self._files[path] = content

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        result: list[tuple[str, bytes]] = []
        for path in paths:
            if path not in self._files:
                raise FileNotFoundError(path)
            result.append((path, self._files[path]))
        return result

    async def close(self) -> None:
        pass


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Force `@pytest.mark.e2e` on every test collected under `tests/e2e/`.

    Makes the directory the source of truth for the marker so a forgotten
    `pytestmark` can't slip past `-m "not e2e"` and run alongside unit
    tests. The directory-is-the-contract rule is mechanically enforced
    by this function.
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
    startup; outside a worktree (CI) the key_prefix defaults to "cubeplex" and
    env to "test", so behavior matches the previous FLUSHDB for a single
    isolated runner.
    """
    if request.node.get_closest_marker("e2e") is None:
        yield
        return
    client: Redis = Redis.from_url(
        _cubeplex_config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=True,
    )
    try:
        # Delete only keys belonging to this worktree's prefix so parallel E2E
        # in other worktrees isn't clobbered. Prefix matches what app.py builds
        # at startup for `app.state.redis_key_prefix`. We read env_name from
        # the SAME source app.py uses (ENV_FOR_DYNACONF) so the two cannot drift.
        base_prefix = _cubeplex_config.get("redis.key_prefix", "cubeplex")
        env_name = os.getenv("ENV_FOR_DYNACONF", "development")
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


DEFAULT_ORG_ID = "org-00000000000000"
DEFAULT_WS_ID = "ws-00000000000000"
DEFAULT_TEST_EMAIL = "test-default@example.com"
DEFAULT_TEST_PASSWORD = "test-default-password-12345"
WS_MEMBER_TEST_EMAIL = "test-ws-member@example.com"
WS_MEMBER_TEST_PASSWORD = "test-ws-member-password-12345"


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
    _cubeplex_db.async_session_maker = test_session_maker

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    return app


def _make_memory_test_app() -> FastAPI:
    """Create a test app with NullPool DB + LocalSandbox (no real DB pool)."""
    url = _build_database_url()
    test_engine = create_async_engine(url, poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    # Patch module-level async_session_maker so DefaultAuthProvider.authenticate
    # (which opens its own session inline) also uses NullPool in tests.
    _cubeplex_db.async_session_maker = test_session_maker

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_maker() as session:
            yield session

    app = create_app(sandbox_factory=None)
    app.dependency_overrides[get_session] = override_get_session
    return app


async def _ensure_default_user_and_membership() -> None:
    """Idempotently ensure a DEFAULT_TEST_EMAIL user exists as admin of DEFAULT_WS_ID.

    Creates DEFAULT_ORG_ID / DEFAULT_WS_ID with fixed IDs if they don't exist yet,
    then creates the user (which also auto-creates a personal org/ws via
    on_after_register — that's fine, both memberships coexist) and grants
    membership to DEFAULT_WS_ID if not already present.
    """
    from cubeplex.models import Organization, Workspace

    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with test_session_maker() as session:
            # Ensure the fixed-ID org exists.
            org_repo = OrganizationRepository(session)
            org = await org_repo.get(DEFAULT_ORG_ID)
            if org is None:
                org = Organization(
                    id=DEFAULT_ORG_ID,
                    name="Test Default Org",
                    slug="test-default-org",
                )
                session.add(org)
                await session.commit()

            # Ensure the fixed-ID workspace exists inside that org.
            ws_repo = WorkspaceRepository(session)
            ws = await ws_repo.get(DEFAULT_WS_ID)
            if ws is None:
                ws = Workspace(
                    id=DEFAULT_WS_ID,
                    org_id=DEFAULT_ORG_ID,
                    name="Test Default Workspace",
                )
                session.add(ws)
                await session.commit()

            # Create the test user (on_after_register will auto-create a
            # second personal org/ws/membership — that is acceptable).
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

            # Ensure membership in the fixed workspace.
            mem_repo = MembershipRepository(session)
            role = await mem_repo.get_role(user_id=user.id, workspace_id=DEFAULT_WS_ID)
            if role is None:
                await mem_repo.grant(user_id=user.id, workspace_id=DEFAULT_WS_ID, role=Role.ADMIN)

            # Ensure org-level OWNER membership for the default user in the fixed
            # default org. M9 admin gates read OrganizationMembership directly;
            # the on_after_register hook only grants OWNER on the user's
            # auto-created personal org, so DEFAULT_ORG_ID needs an explicit grant
            # for routes resolving to it (e.g. /admin/cost via DEFAULT_WS_ID).
            from cubeplex.models import OrgRole
            from cubeplex.repositories import OrganizationMembershipRepository

            om_repo = OrganizationMembershipRepository(session)
            om_role = await om_repo.get_role(user_id=user.id, org_id=DEFAULT_ORG_ID)
            if om_role is None:
                await om_repo.grant(user_id=user.id, org_id=DEFAULT_ORG_ID, role=OrgRole.OWNER)

            # Wipe the multi_tenant bootstrap's auto-created personal org/workspace
            # memberships so resolve_current_org_id (which prefers highest role then
            # oldest created_at) deterministically resolves to DEFAULT_ORG_ID.
            # Both memberships are OWNER, and the personal org is created first,
            # so without this it wins the tiebreaker and /admin/cost queries the
            # wrong org.
            from sqlalchemy import delete

            from cubeplex.models import Membership as MembershipModel
            from cubeplex.models import OrganizationMembership

            await session.execute(
                delete(OrganizationMembership).where(
                    OrganizationMembership.user_id == user.id,  # type: ignore[arg-type]
                    OrganizationMembership.org_id != DEFAULT_ORG_ID,  # type: ignore[arg-type]
                )
            )
            await session.execute(
                delete(MembershipModel).where(
                    MembershipModel.user_id == user.id,  # type: ignore[arg-type]
                    MembershipModel.workspace_id != DEFAULT_WS_ID,  # type: ignore[arg-type]
                )
            )
            await session.commit()
    finally:
        await test_engine.dispose()


async def _ensure_default_ws_member() -> None:
    """Idempotently ensure a plain-member user exists inside DEFAULT_WS_ID.

    Used to test owner-or-admin gating: this user is a MEMBER of the same
    workspace as the default admin, so requests resolve into DEFAULT_WS_ID
    without admin powers.
    """
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with test_session_maker() as session:
            user_db = SQLAlchemyUserDatabase(session, User)
            existing = await user_db.get_by_email(WS_MEMBER_TEST_EMAIL)
            if existing is None:
                manager = UserManager(user_db)
                user = await manager.create(
                    BaseUserCreate(
                        email=WS_MEMBER_TEST_EMAIL,
                        password=WS_MEMBER_TEST_PASSWORD,
                    ),
                    safe=False,
                )
            else:
                user = existing

            mem_repo = MembershipRepository(session)
            role = await mem_repo.get_role(user_id=user.id, workspace_id=DEFAULT_WS_ID)
            if role is None:
                await mem_repo.grant(user_id=user.id, workspace_id=DEFAULT_WS_ID, role=Role.MEMBER)

            # Ensure org-level membership so resolve_current_org_id picks
            # DEFAULT_ORG_ID for this user too (mirror the admin's setup).
            from cubeplex.models import OrgRole
            from cubeplex.repositories import OrganizationMembershipRepository

            om_repo = OrganizationMembershipRepository(session)
            om_role = await om_repo.get_role(user_id=user.id, org_id=DEFAULT_ORG_ID)
            if om_role is None:
                await om_repo.grant(user_id=user.id, org_id=DEFAULT_ORG_ID, role=OrgRole.MEMBER)

            # Same cleanup as the admin fixture: wipe bootstrap-created personal
            # org/ws memberships so DEFAULT_ORG_ID/WS resolves deterministically.
            from sqlalchemy import delete

            from cubeplex.models import Membership as MembershipModel
            from cubeplex.models import OrganizationMembership

            await session.execute(
                delete(OrganizationMembership).where(
                    OrganizationMembership.user_id == user.id,  # type: ignore[arg-type]
                    OrganizationMembership.org_id != DEFAULT_ORG_ID,  # type: ignore[arg-type]
                )
            )
            await session.execute(
                delete(MembershipModel).where(
                    MembershipModel.user_id == user.id,  # type: ignore[arg-type]
                    MembershipModel.workspace_id != DEFAULT_WS_ID,  # type: ignore[arg-type]
                )
            )
            await session.commit()
    finally:
        await test_engine.dispose()


async def _login_and_attach(client: httpx.AsyncClient, email: str, password: str) -> None:
    """Log in and set the CSRF header on the client."""
    await client.get("/api/v1/auth/me")  # obtain CSRF cookie (401 but sets cookie)
    csrf = client.cookies.get(csrf_cookie_name()) or ""
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"
    client.headers["X-CSRF-Token"] = client.cookies.get(csrf_cookie_name()) or csrf


@pytest_asyncio.fixture
async def client() -> AsyncIterator[TestClient]:
    """Sync test client, auto-logged-in as default user in default-ws.

    Business-scoped calls should be prefixed with `/api/v1/ws/{DEFAULT_WS_ID}/...`.
    """
    await _ensure_default_user_and_membership()
    app = _make_test_app()
    # Force multi_tenant mode BEFORE lifespan starts so the startup
    # mode-consistency check sees the correct value.
    app.state.deployment_mode = "multi_tenant"
    # TestClient as a context manager runs the FastAPI lifespan — required
    # since auth routers are now mounted at lifespan startup (not in create_app).
    with TestClient(app) as sync_client:
        sync_client.get("/api/v1/auth/me")  # obtain CSRF cookie
        csrf = sync_client.cookies.get(csrf_cookie_name()) or ""
        r = sync_client.post(
            "/api/v1/auth/login",
            data={"username": DEFAULT_TEST_EMAIL, "password": DEFAULT_TEST_PASSWORD},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"
        sync_client.headers["X-CSRF-Token"] = sync_client.cookies.get(csrf_cookie_name()) or csrf
        yield sync_client


@pytest_asyncio.fixture
async def ws_member_client() -> AsyncIterator[TestClient]:
    """Sync TestClient logged in as a plain MEMBER of DEFAULT_WS_ID.

    Distinct from `member_client` (which creates an isolated brand-new
    workspace). Use this when the test needs an admin AND a non-admin in
    the SAME workspace — e.g. owner-or-admin mutation gating tests.
    """
    await _ensure_default_user_and_membership()  # seed admin user/ws first
    await _ensure_default_ws_member()
    app = _make_test_app()
    app.state.deployment_mode = "multi_tenant"
    with TestClient(app) as sync_client:
        sync_client.get("/api/v1/auth/me")
        csrf = sync_client.cookies.get(csrf_cookie_name()) or ""
        r = sync_client.post(
            "/api/v1/auth/login",
            data={
                "username": WS_MEMBER_TEST_EMAIL,
                "password": WS_MEMBER_TEST_PASSWORD,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"
        sync_client.headers["X-CSRF-Token"] = sync_client.cookies.get(csrf_cookie_name()) or csrf
        yield sync_client


@pytest_asyncio.fixture
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    """Async HTTP client, auto-logged-in as default user in default-ws.

    Business-scoped calls should be prefixed with `/api/v1/ws/{DEFAULT_WS_ID}/...`.
    """
    await _ensure_default_user_and_membership()
    app = _make_test_app()
    # Force multi_tenant mode BEFORE lifespan starts so the startup
    # mode-consistency check sees the correct value.
    app.state.deployment_mode = "multi_tenant"
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, DEFAULT_TEST_EMAIL, DEFAULT_TEST_PASSWORD)
            yield c
    await engine.dispose()


@pytest_asyncio.fixture
async def memory_client() -> AsyncIterator[httpx.AsyncClient]:
    """Async client using NullPool DB + LocalSandbox, auto-logged-in."""
    await _ensure_default_user_and_membership()
    app = _make_memory_test_app()
    # Force multi_tenant mode BEFORE lifespan starts so the startup
    # mode-consistency check sees the correct value.
    app.state.deployment_mode = "multi_tenant"
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
    # Force multi_tenant mode BEFORE lifespan starts so the startup
    # mode-consistency check sees the correct value.
    app.state.deployment_mode = "multi_tenant"
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

    from cubeplex.auth.users import _slugify_org_name
    from cubeplex.models import OrgRole
    from cubeplex.repositories import OrganizationMembershipRepository

    org_name = f"Org {email}"
    org = await org_repo.create(name=org_name, slug=_slugify_org_name(org_name))
    ws = await ws_repo.create(org_id=org.id, name=f"WS {email}")

    password = secrets.token_urlsafe(16)
    user_db = SQLAlchemyUserDatabase(session, User)
    manager = UserManager(user_db)
    user = await manager.create(BaseUserCreate(email=email, password=password), safe=False)

    # multi_tenant bootstrap auto-creates a personal org/workspace for the user
    # and grants them OrgRole.OWNER + workspace-admin there. Tests want the user
    # scoped to exactly the explicit `org`/`ws` created above, so wipe any
    # bootstrap-created memberships in other orgs.
    from sqlalchemy import delete

    from cubeplex.models import Membership as MembershipModel
    from cubeplex.models import OrganizationMembership

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

    await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=role)
    org_role = OrgRole.OWNER if role == Role.ADMIN else OrgRole.MEMBER
    await OrganizationMembershipRepository(session).grant(
        user_id=user.id, org_id=org.id, role=org_role
    )
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
    # Force multi_tenant mode BEFORE lifespan starts so the startup
    # mode-consistency check sees the correct value.
    app.state.deployment_mode = "multi_tenant"
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
async def seeded_credential_with_host(
    admin_client: tuple[httpx.AsyncClient, str],
) -> dict[str, Any]:
    """Seed an org-scope SandboxEnvVar with hosts=['api.github.com'].

    Uses the real admin POST /admin/sandbox-env endpoint (same client / same
    org as the test's ``admin_client``), so the row lands in the right org.
    Returns the created entry dict (includes ``id``, ``env_name``, ``hosts``).
    """
    client, _ws = admin_client
    resp = await client.post(
        "/api/v1/admin/sandbox-env",
        json={
            "env_name": "POLICY_TEST_TOKEN",
            "is_secret": True,
            "hosts": ["api.github.com"],
            "secret_value": "ghp_seed",
        },
    )
    assert resp.status_code == 201, resp.text
    entry: dict[str, Any] = resp.json()
    entry["host"] = "api.github.com"
    return entry


@pytest_asyncio.fixture
async def member_client() -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Fresh client logged in as a brand-new member (not admin) of a brand-new workspace."""
    app, email, password, workspace_id = await _make_isolated_user(Role.MEMBER)
    # Force multi_tenant mode BEFORE lifespan starts so the startup
    # mode-consistency check sees the correct value.
    app.state.deployment_mode = "multi_tenant"
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, workspace_id


@pytest_asyncio.fixture
async def non_admin_client() -> AsyncIterator[httpx.AsyncClient]:
    """Fresh client logged in as a brand-new member (not admin).

    Yields just the ``httpx.AsyncClient`` (no workspace_id) for tests that
    only need to assert RBAC rejection on admin routes.
    """
    app, email, password, _workspace_id = await _make_isolated_user(Role.MEMBER)
    app.state.deployment_mode = "multi_tenant"
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c


@pytest_asyncio.fixture
async def member_client_two_workspaces() -> AsyncIterator[tuple[httpx.AsyncClient, str, str]]:
    """Fresh member user with two workspaces in the same org.

    Yields ``(client, ws_a, ws_b)``.  ws_a is the primary workspace created by
    ``_make_isolated_user``; ws_b is a second workspace in the same org granted
    to the same user.  Used to verify workspace-private installs don't bleed.
    """
    app, email, password, ws_a = await _make_isolated_user(Role.MEMBER)

    # Open a short-lived session to look up org_id (from ws_a) and create ws_b.
    setup_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    setup_maker = async_sessionmaker(setup_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with setup_maker() as session:
            from sqlalchemy import select as sa_select

            from cubeplex.models import User as UserModel
            from cubeplex.models import Workspace as WorkspaceModel

            ws_row = await session.get(WorkspaceModel, ws_a)
            assert ws_row is not None
            org_id = ws_row.org_id

            user_result = await session.execute(
                sa_select(UserModel).where(UserModel.email == email)  # type: ignore[arg-type]
            )
            user = user_result.scalar_one()

            ws_b_row = await WorkspaceRepository(session).create(org_id=org_id, name="ws-b")
            ws_b = ws_b_row.id
            await MembershipRepository(session).grant(
                user_id=user.id, workspace_id=ws_b, role=Role.MEMBER
            )
            await session.commit()
    finally:
        await setup_engine.dispose()

    app.state.deployment_mode = "multi_tenant"
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, ws_a, ws_b


@pytest_asyncio.fixture
async def member_client_org_a() -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Fresh org A with a member user."""
    app, email, password, workspace_id = await _make_isolated_user(Role.MEMBER)
    # Force multi_tenant mode BEFORE lifespan starts so the startup
    # mode-consistency check sees the correct value.
    app.state.deployment_mode = "multi_tenant"
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, workspace_id


@pytest_asyncio.fixture
async def member_client_org_b() -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Fresh org B with a member user — distinct from org A."""
    app, email, password, workspace_id = await _make_isolated_user(Role.MEMBER)
    # Force multi_tenant mode BEFORE lifespan starts so the startup
    # mode-consistency check sees the correct value.
    app.state.deployment_mode = "multi_tenant"
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

    from cubeplex.models import Artifact, Conversation, Membership, Workspace
    from cubeplex.objectstore import get_objectstore_client

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
    # Force multi_tenant mode BEFORE lifespan starts so the startup
    # mode-consistency check sees the correct value.
    app.state.deployment_mode = "multi_tenant"
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
    # Force multi_tenant mode BEFORE lifespan starts so the startup
    # mode-consistency check sees the correct value.
    app.state.deployment_mode = "multi_tenant"
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
        _cubeplex_config.get("redis.url", "redis://127.0.0.1:6379/0"),
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
def stub_discover_tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No-op MCP discovery stub for tests that document "this run does not
    hit the network".

    The four-layer install service itself doesn't run synchronous
    discovery on install create, but the static-grant POST routes
    (admin + workspace) call ``run_post_grant_discovery`` so the operator
    gets immediate feedback on whether the credential works against the
    MCP server. For tests whose URLs point at fake hosts that resolve
    to ``ConnectError``, that probe persists ``discovery_status='error'``
    and breaks assertions about ``usable=True``. Patch both route-local
    bindings (they import the symbol by name, so patching the service
    module alone is not enough) plus the OAuth callback's local import.
    """

    async def _noop(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr("cubeplex.services.mcp_discovery.run_post_grant_discovery", _noop)
    monkeypatch.setattr("cubeplex.api.routes.v1.admin_mcp.run_post_grant_discovery", _noop)
    monkeypatch.setattr("cubeplex.api.routes.v1.ws_mcp.run_post_grant_discovery", _noop)
    yield


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


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yields async_sessionmaker for direct DB access in repo tests."""
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def fresh_db_unauth_client_single_tenant() -> AsyncIterator[httpx.AsyncClient]:
    """Fresh test DB; deployment.mode=single_tenant; no pre-seeded user."""
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with test_session_maker() as session:
        await session.execute(text("TRUNCATE TABLE organization_memberships CASCADE"))
        await session.execute(text("TRUNCATE TABLE memberships CASCADE"))
        await session.execute(text("TRUNCATE TABLE workspaces CASCADE"))
        await session.execute(text("TRUNCATE TABLE organizations CASCADE"))
        await session.execute(text("TRUNCATE TABLE users CASCADE"))
        await session.commit()
    await test_engine.dispose()

    app = _make_memory_test_app()
    async with _lifespan_context(app):
        # Override deployment_mode after lifespan startup so the lifespan's
        # config-based assignment doesn't overwrite our test value.
        app.state.deployment_mode = "single_tenant"
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


# ---------------------------------------------------------------------------
# Four-layer MCP fixtures.
# ---------------------------------------------------------------------------
#
# These fixtures seed connector templates and provide a same-workspace
# admin+member client pair used by the four-layer route E2E tests.


async def _seed_four_layer_template(
    *,
    slug: str,
    name: str,
    supported_auth_methods: list[str],
    default_credential_policy: str,
    template_metadata: dict[str, Any] | None = None,
    static_form_schema: list[dict[str, Any]] | None = None,
    server_url: str | None = None,
) -> str:
    """Idempotent template upsert for E2E setup. Returns the template id.

    ``server_url`` defaults to the slug-derived URL. Override it to seed
    two templates pointing at the same URL — useful for cross-template
    URL uniqueness tests.
    """
    from cubeplex.repositories.mcp import MCPConnectorTemplateRepository

    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            repo = MCPConnectorTemplateRepository(session)
            tpl = await repo.upsert_by_slug(
                slug=slug,
                name=name,
                description=f"E2E template '{slug}' for four-layer MCP tests.",
                provider="e2e",
                server_url=server_url or f"https://{slug}.example.com/mcp",
                transport="streamable_http",
                supported_auth_methods=supported_auth_methods,
                default_credential_policy=default_credential_policy,
                static_form_schema=static_form_schema,
                template_metadata=template_metadata or {},
                status="active",
            )
            await session.commit()
            return tpl.id
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def noauth_template_id() -> str:
    """Template with no-auth method + ``credential_policy='none'``."""
    return await _seed_four_layer_template(
        slug="noauth-e2e",
        name="No-Auth E2E Connector",
        supported_auth_methods=["none"],
        default_credential_policy="none",
    )


@pytest_asyncio.fixture
async def static_template_id() -> str:
    """Template with static auth + ``credential_policy='user'`` default."""
    return await _seed_four_layer_template(
        slug="static-e2e",
        name="Static E2E Connector",
        supported_auth_methods=["static"],
        default_credential_policy="user",
        static_form_schema=[
            {"name": "token", "label": "API Token", "type": "password", "required": True}
        ],
    )


@pytest_asyncio.fixture
async def oauth_template_id() -> str:
    """Template advertising OAuth — used by the OAuth-refresh test (currently skipped)."""
    return await _seed_four_layer_template(
        slug="oauth-e2e",
        name="OAuth E2E Connector",
        supported_auth_methods=["oauth"],
        default_credential_policy="user",
        template_metadata={
            "oauth": {
                "authorization_endpoint": "https://oauth-e2e.example.com/authorize",
                "token_endpoint": "https://oauth-e2e.example.com/token",
                "scopes": ["read"],
            },
        },
    )


async def _seed_same_ws_admin_and_member(
    role_for_b: Role = Role.MEMBER,
) -> tuple[FastAPI, str, str, str, str, str]:
    """Seed two users in the same workspace.

    Returns ``(app, admin_email, admin_password, member_email, member_password,
    workspace_id)``.

    User A is the workspace admin (and org owner); user B is added to the same
    workspace with ``role_for_b`` (default ``Role.MEMBER``). Both users keep their
    auto-bootstrap personal org/workspace deleted so ``resolve_current_org_id``
    deterministically picks the shared org for each.
    """
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with test_session_maker() as session:
            admin_email = f"4l-admin-{secrets.token_hex(4)}@example.com"
            _admin, workspace_id, admin_password = await _ensure_test_user_membership(
                session, email=admin_email, role=Role.ADMIN
            )

            # Add user B to the SAME workspace + org as a member.
            from sqlalchemy import delete
            from sqlalchemy import select as sa_select

            from cubeplex.models import Membership as MembershipModel
            from cubeplex.models import OrganizationMembership, OrgRole, Workspace

            ws = await session.get(Workspace, workspace_id)
            assert ws is not None
            org_id = ws.org_id

            member_email = f"4l-member-{secrets.token_hex(4)}@example.com"
            member_password = secrets.token_urlsafe(16)
            user_db = SQLAlchemyUserDatabase(session, User)
            manager = UserManager(user_db)
            member_user = await manager.create(
                BaseUserCreate(email=member_email, password=member_password),
                safe=False,
            )

            # Wipe member's bootstrap personal org + workspace memberships so
            # resolve_current_org_id picks our shared org for them.
            await session.execute(
                delete(OrganizationMembership).where(
                    OrganizationMembership.user_id == member_user.id,  # type: ignore[arg-type]
                    OrganizationMembership.org_id != org_id,  # type: ignore[arg-type]
                )
            )
            await session.execute(
                delete(MembershipModel).where(
                    MembershipModel.user_id == member_user.id,  # type: ignore[arg-type]
                    MembershipModel.workspace_id != workspace_id,  # type: ignore[arg-type]
                )
            )
            await session.commit()

            mem_repo = MembershipRepository(session)
            await mem_repo.grant(user_id=member_user.id, workspace_id=workspace_id, role=role_for_b)
            from cubeplex.repositories import OrganizationMembershipRepository

            om_repo = OrganizationMembershipRepository(session)
            existing_om = await om_repo.get_role(user_id=member_user.id, org_id=org_id)
            if existing_om is None:
                await om_repo.grant(user_id=member_user.id, org_id=org_id, role=OrgRole.MEMBER)
            # Sanity: confirm membership rows exist.
            stmt = sa_select(MembershipModel).where(
                MembershipModel.user_id == member_user.id,  # type: ignore[arg-type]
                MembershipModel.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
            assert (await session.execute(stmt)).scalars().first() is not None
            await session.commit()
    finally:
        await test_engine.dispose()

    app = _make_memory_test_app()
    return app, admin_email, admin_password, member_email, member_password, workspace_id


@pytest_asyncio.fixture
async def four_layer_admin_and_member() -> AsyncIterator[
    tuple[
        tuple[httpx.AsyncClient, str, str],
        tuple[httpx.AsyncClient, str, str],
    ]
]:
    """Two clients in the SAME workspace.

    Yields ``((admin_client, workspace_id, admin_user_id),
    (member_client, workspace_id, member_user_id))``. The admin holds workspace
    ``Role.ADMIN`` + org ``OrgRole.OWNER``; the member is workspace ``Role.MEMBER`` +
    org ``OrgRole.MEMBER``. Both share the same workspace and org, which is the
    setup required by the user-policy isolation E2E (spec test #3) — the existing
    ``admin_client`` / ``member_client`` fixtures live in *different* workspaces
    and can't express this scenario.
    """
    (
        app,
        admin_email,
        admin_password,
        member_email,
        member_password,
        workspace_id,
    ) = await _seed_same_ws_admin_and_member()

    # Force multi_tenant mode BEFORE lifespan starts so the startup
    # mode-consistency check sees the correct value.
    app.state.deployment_mode = "multi_tenant"
    async with _lifespan_context(app):
        transport_a = httpx.ASGITransport(app=app)
        transport_b = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport_a, base_url="http://test") as admin_c,
            httpx.AsyncClient(transport=transport_b, base_url="http://test") as member_c,
        ):
            await _login_and_attach(admin_c, admin_email, admin_password)
            await _login_and_attach(member_c, member_email, member_password)

            admin_me = await admin_c.get("/api/v1/auth/me")
            assert admin_me.status_code == 200, admin_me.text
            admin_user_id = admin_me.json()["id"]
            member_me = await member_c.get("/api/v1/auth/me")
            assert member_me.status_code == 200, member_me.text
            member_user_id = member_me.json()["id"]

            yield (
                (admin_c, workspace_id, admin_user_id),
                (
                    member_c,
                    workspace_id,
                    member_user_id,
                ),
            )


# ---------------------------------------------------------------------------
# Sandbox scoping E2E fixtures (Task 9).
#
# ``fake_opensandbox`` monkeypatches ``opensandbox.Sandbox.create`` and
# ``.connect`` with the same ``_FakeRaw`` shim used by Task 6's unit test, so
# DB-level isolation assertions never touch a real provider. The fakes only
# satisfy the calls SandboxManager.get_or_create makes on the no-egress /
# no-volume default path (``id`` attr, ``is_healthy``, ``close``, ``kill``).
# ---------------------------------------------------------------------------


class _FakeLog:
    def __init__(self, text: str = "") -> None:
        self.text = text


class _FakeExecution:
    def __init__(self) -> None:
        self.id: str | None = None
        self.logs = SimpleNamespace(stdout=[], stderr=[])


class _FakeCommandStatus:
    def __init__(self) -> None:
        self.exit_code: int = 0


class _FakeCommands:
    async def run(self, command: str, *, opts: object = None) -> _FakeExecution:
        del command, opts
        return _FakeExecution()

    async def get_command_status(self, execution_id: str) -> _FakeCommandStatus:
        del execution_id
        return _FakeCommandStatus()


class _FakeFiles:
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def write_file(self, path: str, content: bytes) -> None:
        self._store[path] = content

    async def read_bytes(self, path: str) -> bytes:
        if path not in self._store:
            raise Exception(f"404: {path} not found")
        return self._store[path]


class _FakeRaw:
    """Minimal stand-in for ``opensandbox.Sandbox`` used by Task 9 E2E.

    IDs are randomized per-instance (``token_hex``) instead of a process-local
    counter so re-running the E2E doesn't collide with leftover rows from a
    prior run that survived in the persistent test DB.

    ``commands`` and ``files`` are fake sub-objects so that ``OpenSandbox``
    wrappers (and therefore ``LazySandbox``) can call ``execute`` /
    ``upload`` / ``download`` without raising ``AttributeError``.  They use
    an in-memory dict for file storage — sufficient for materialising the
    LazySandbox in ``fresh_workspace_and_sandbox``.
    """

    def __init__(self) -> None:
        self.id = f"prov-{secrets.token_hex(6)}"
        self.commands = _FakeCommands()
        self.files = _FakeFiles()

    async def is_healthy(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    async def kill(self) -> None:
        return None


@pytest.fixture
def fake_opensandbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Patch opensandbox.Sandbox.create/connect with ``_FakeRaw`` fakes."""
    import opensandbox

    async def _fake_create(image: str, **kwargs: Any) -> _FakeRaw:
        del image, kwargs
        return _FakeRaw()

    async def _fake_connect(sandbox_id: str, **kwargs: Any) -> _FakeRaw:
        del sandbox_id, kwargs
        return _FakeRaw()

    monkeypatch.setattr(opensandbox.Sandbox, "create", staticmethod(_fake_create))
    monkeypatch.setattr(opensandbox.Sandbox, "connect", staticmethod(_fake_connect))
    yield


@pytest_asyncio.fixture
async def seeded_org_ws_user() -> AsyncIterator[tuple[str, str, str, str]]:
    """One org, two workspaces, one user (member of both).

    Yields ``(org_id, ws_a_id, ws_b_id, user_id)``. Used by the
    ownership-isolation E2E to drive ``SandboxManager.get_or_create`` for the
    same user across two distinct workspaces. The user is wiped of any
    bootstrap personal org/workspace so the only memberships are the two we
    create here.
    """
    from sqlalchemy import delete

    from cubeplex.auth.users import _slugify_org_name
    from cubeplex.models import Membership as MembershipModel
    from cubeplex.models import OrganizationMembership, OrgRole
    from cubeplex.repositories import OrganizationMembershipRepository

    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with test_session_maker() as session:
            email = f"sbx-scope-{secrets.token_hex(4)}@example.com"
            org_name = f"Org {email}"
            org = await OrganizationRepository(session).create(
                name=org_name, slug=_slugify_org_name(org_name)
            )
            ws_repo = WorkspaceRepository(session)
            ws_a = await ws_repo.create(org_id=org.id, name=f"WS-A {email}")
            ws_b = await ws_repo.create(org_id=org.id, name=f"WS-B {email}")

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
                    MembershipModel.workspace_id.notin_([ws_a.id, ws_b.id]),  # type: ignore[attr-defined]
                )
            )
            await session.commit()

            mem_repo = MembershipRepository(session)
            await mem_repo.grant(user_id=user.id, workspace_id=ws_a.id, role=Role.ADMIN)
            await mem_repo.grant(user_id=user.id, workspace_id=ws_b.id, role=Role.ADMIN)
            om_repo = OrganizationMembershipRepository(session)
            existing_om = await om_repo.get_role(user_id=user.id, org_id=org.id)
            if existing_om is None:
                await om_repo.grant(user_id=user.id, org_id=org.id, role=OrgRole.OWNER)
            await session.commit()

            yield (org.id, ws_a.id, ws_b.id, str(user.id))
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def admin_client_with_user_id() -> AsyncIterator[tuple[httpx.AsyncClient, str, str]]:
    """Like ``admin_client`` but also exposes the logged-in user's id.

    Yields ``(client, workspace_id, user_id)``. Used by the command-deny E2E
    to bind the middleware/audit buffer to the same workspace the admin call
    targets.
    """
    app, email, password, workspace_id = await _make_isolated_user(Role.ADMIN)
    app.state.deployment_mode = "multi_tenant"
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            me = await c.get("/api/v1/auth/me")
            assert me.status_code == 200, me.text
            user_id = me.json()["id"]
            yield c, workspace_id, user_id


@pytest_asyncio.fixture
async def seeded_session_org_ws() -> AsyncIterator[tuple[AsyncSession, str, str, str]]:
    """Raw AsyncSession + a fresh org/workspace, with preinstalled skills seeded.

    Yields ``(session, org_id, org_slug, workspace_id)``.  Suitable for
    repository/service-layer E2E tests that need direct DB access and the
    preinstalled skill catalog (e.g. find_skills tool unit-integration tests).
    """
    from cubeplex.auth.users import _slugify_org_name
    from cubeplex.config import backend_dir
    from cubeplex.config import config as _cfg
    from cubeplex.models import Organization, Workspace
    from cubeplex.seeders import seed_preinstalled_skills

    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Seed preinstalled skills via the real seeder (Redis lock, real object store).
    redis: Redis = Redis.from_url(
        _cubeplex_config.get("redis.url", "redis://127.0.0.1:6379/0"),
        decode_responses=False,
    )
    try:
        async with maker() as seed_session:
            preinstalled_rel = _cfg.get("skills.preinstalled_dir", "skills/preinstalled")
            preinstalled_dir = Path(backend_dir) / preinstalled_rel
            await seed_preinstalled_skills(
                preinstalled_dir=preinstalled_dir,
                db_session=seed_session,
                redis=redis,
            )
    finally:
        await redis.aclose()

    # Create a fresh org + workspace for test isolation.
    org_name = f"seeded-org-{secrets.token_hex(4)}"
    org_slug = _slugify_org_name(org_name)
    try:
        async with maker() as setup_session:
            org = Organization(name=org_name, slug=org_slug)
            setup_session.add(org)
            await setup_session.flush()
            ws = Workspace(org_id=org.id, name="seeded-ws")
            setup_session.add(ws)
            await setup_session.commit()
            org_id = org.id
            ws_id = ws.id
    finally:
        pass  # engine disposed below

    try:
        async with maker() as session:
            yield session, org_id, org_slug, ws_id
    finally:
        await test_engine.dispose()


_FAKE_SKILL_MD = (
    "---\nname: slide-deck\ndescription: Build slide decks\nversion: 1.0.0\n---\n# Slide deck\n"
)


@pytest_asyncio.fixture
async def fake_registry_url() -> AsyncIterator[str]:
    """Stand up a real local HTTP registry server and yield its base URL.

    The server implements the three endpoints that RemoteRegistryAdapter calls:
    GET /search, GET /tree/{ref:path}, GET /raw/{full:path}. Using a real
    uvicorn server (not a MockTransport) exercises the full production httpx
    code path inside RemoteRegistryAdapter.
    """
    registry_app = FastAPI()

    @registry_app.get("/search")
    async def _search(q: str = "", limit: int = 5) -> dict[str, object]:
        return {
            "skills": [
                {
                    "name": "slide-deck",
                    "description": "Build slide decks",
                    "keywords": ["slides", "deck"],
                    "ref": "acme/skills/tree/main/skills/slide-deck",
                    "stars": 1200,
                    "installs": 50,
                }
            ]
        }

    @registry_app.get("/tree/{ref:path}")
    async def _tree(ref: str) -> dict[str, object]:
        return {"files": ["SKILL.md", "references/style.md"]}

    @registry_app.get("/raw/{full:path}")
    async def _raw(full: str) -> PlainTextResponse:
        if full.endswith("/SKILL.md") or full == "SKILL.md":
            return PlainTextResponse(_FAKE_SKILL_MD)
        if full.endswith("style.md"):
            return PlainTextResponse("# style guide\n")
        raise HTTPException(status_code=404, detail="not found")

    # Grab an ephemeral port before starting the server so we know the URL.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cfg = uvicorn.Config(
        registry_app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(cfg)
    task = asyncio.create_task(server.serve())

    # Wait until uvicorn signals it has bound and is ready.
    deadline = 5.0
    elapsed = 0.0
    while not server.started and elapsed < deadline:
        await asyncio.sleep(0.02)
        elapsed += 0.02

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


# ---------------------------------------------------------------------------
# Conversation-search fixtures (moved from tests/services/conversation_search/conftest.py).
#
# Lives here because the underlying tests now sit in tests/e2e/ — that keeps
# the unit-tier CI job (which runs without Postgres) from collecting them and
# hitting connection errors at import time. The DB-touching cleanup is scoped
# to the seed fixtures rather than module-level autouse, so non-search e2e
# tests don't pay for an extra TRUNCATE per test.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _clean_search_tables() -> AsyncIterator[None]:
    """Clear search-derived tables before each search test.

    Without this, orphan rows from prior failed runs (``embedding_jobs.state =
    pending`` referencing now-vanished conversations) get claimed first by the
    worker under test, starving the test's own enqueue.
    """
    from cubeplex.db.engine import async_session_maker as _asm

    async with _asm() as session:
        await session.execute(text("TRUNCATE TABLE embedding_jobs RESTART IDENTITY"))
        await session.execute(text("TRUNCATE TABLE conversation_chunks RESTART IDENTITY"))
        await session.commit()
    yield


@pytest_asyncio.fixture
async def search_test_user_ctx(_clean_search_tables: None) -> tuple[str, str, str]:
    """Create a minimal org / workspace / user trio and return their IDs.

    Bypasses the fastapi_users registration flow — search tests don't
    authenticate, they just need scope IDs that satisfy FK constraints on
    Conversation / ConversationChunk / EmbeddingJob. The slug and email are
    randomized so concurrent / repeated runs don't collide.
    """
    from cubeplex.db.engine import async_session_maker as _asm
    from cubeplex.models.organization import Organization
    from cubeplex.models.user import User as UserModel
    from cubeplex.models.workspace import Workspace

    suffix = secrets.token_hex(6)
    async with _asm() as session:
        org = Organization(name=f"search-test-{suffix}", slug=f"search-test-{suffix}")
        session.add(org)
        await session.commit()
        await session.refresh(org)
        ws = Workspace(org_id=org.id, name="search-test-ws")
        session.add(ws)
        await session.commit()
        await session.refresh(ws)
        user = UserModel(
            email=f"search-test-{suffix}@example.com",
            hashed_password="x",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return org.id, ws.id, user.id


@pytest_asyncio.fixture
async def seeded_conversation(
    search_test_user_ctx: tuple[str, str, str],
) -> tuple[str, str, str, str]:
    """Create a conversation and seed three small cubepi messages."""
    from cubepi.providers.base import AssistantMessage, TextContent, UserMessage

    from cubeplex.agents.checkpointer import init_checkpointer
    from cubeplex.db.engine import async_session_maker as _asm
    from cubeplex.models.conversation import Conversation

    org_id, ws_id, user_id = search_test_user_ctx
    async with _asm() as session:
        c = Conversation(
            org_id=org_id,
            workspace_id=ws_id,
            creator_user_id=user_id,
            title="seed",
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        conv_id = c.id
    async with init_checkpointer() as cp:
        await cp.append(
            conv_id,
            [
                UserMessage(content=[TextContent(text="hello docling")], timestamp=1.0),
                AssistantMessage(content=[TextContent(text="hi there")], timestamp=2.0),
                UserMessage(content=[TextContent(text="文档解析问题")], timestamp=3.0),
            ],
        )
    return org_id, ws_id, user_id, conv_id


@pytest_asyncio.fixture
async def seed_conversations_with_content(
    search_test_user_ctx: tuple[str, str, str],
) -> tuple[str, str, str, list[tuple[str, str]]]:
    """Seed three conversations: English keyword, Chinese keyword, unrelated.

    Returns ``(org_id, workspace_id, user_id, [(conv_id, gist), ...])`` so
    callers can drive embedding + assert which conversation they expect to
    find for each search query.
    """
    from cubepi.providers.base import AssistantMessage, TextContent, UserMessage

    from cubeplex.agents.checkpointer import init_checkpointer
    from cubeplex.db.engine import async_session_maker as _asm
    from cubeplex.models.conversation import Conversation

    org_id, ws_id, user_id = search_test_user_ctx
    seeds: list[tuple[str, list[TextContent], str]] = [
        (
            "docling-en",
            [TextContent(text="docling is a PDF parser for agent pipelines")],
            "english docling",
        ),
        (
            "docling-zh",
            [TextContent(text="docling 是一款用于智能体的文档解析工具")],
            "chinese 文档解析",
        ),
        (
            "unrelated",
            [TextContent(text="weather is sunny today, no parsing here")],
            "unrelated",
        ),
    ]
    out: list[tuple[str, str]] = []
    for title, user_content, gist in seeds:
        async with _asm() as session:
            c = Conversation(
                org_id=org_id,
                workspace_id=ws_id,
                creator_user_id=user_id,
                title=title,
            )
            session.add(c)
            await session.commit()
            await session.refresh(c)
            conv_id = c.id
        async with init_checkpointer() as cp:
            await cp.append(
                conv_id,
                [
                    UserMessage(content=user_content, timestamp=1.0),
                    AssistantMessage(content=[TextContent(text="ack")], timestamp=2.0),
                ],
            )
        out.append((conv_id, gist))
    return org_id, ws_id, user_id, out


@pytest_asyncio.fixture
async def seed_remote_source() -> AsyncIterator[Callable[..., Awaitable[str]]]:
    """Insert a SkillRegistry row directly, returning its id.

    The admin route ``POST /admin/skill-registries`` now rejects loopback/private
    hosts as SSRF (``BAD_BASE_URL``), which is exactly what the ``fake_registry_url``
    test server binds to. Remote-discovery E2Es don't exercise that validation
    (it's covered by ``test_create_rejects_ssrf_base_urls``); they only need a
    registered source, so they seed one straight into the DB.
    """
    from cubeplex.repositories.skill_registry import SkillRegistryRepository

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _seed(
        *,
        workspace_id: str,
        created_by_user_id: str,
        base_url: str,
        name: str = "fake",
        trust_tier: str = "community",
        repo: str | None = None,
    ) -> str:
        async with maker() as session:
            ws = await WorkspaceRepository(session).get(workspace_id)
            assert ws is not None
            row = await SkillRegistryRepository(session).create(
                org_id=ws.org_id,
                name=name,
                kind="remote",
                base_url=base_url,
                repo=repo,
                trust_tier=trust_tier,
                created_by_user_id=created_by_user_id,
            )
            await session.commit()
            return row.id

    try:
        yield _seed
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Sandbox-skills-sync shared fixtures + helpers (Tasks 2.10-2.14).
#
# ``fresh_workspace_and_sandbox`` yields a brand-new org/workspace/user trio
# plus a materialised LazySandbox. The two helper functions are plain async
# callables (not fixtures) because callers need to pass different slugs and
# use the same db_session opened for the test's assertions.
#
# Design choices:
#   - ``fake_opensandbox`` is required by the fixture to avoid real provider
#     calls; the fixture depends on it so the monkeypatch is active for the
#     whole fixture lifetime.
#   - SandboxManager is constructed inline (mirrors test_sandbox_scoping.py).
#   - SkillCache uses a per-call tempfile.mkdtemp so there is no pytest
#     tmp_path dependency at fixture level.
#   - S3 uploads in ``install_skill_for_workspace`` are real (rustfs :9010).
# ---------------------------------------------------------------------------

_SYNC_ENCRYPTION_BACKEND = FernetBackend([Fernet.generate_key()])


@pytest_asyncio.fixture
async def fresh_workspace_and_sandbox(
    fake_opensandbox: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[SimpleNamespace]:
    """Brand-new org/workspace/user + a materialised LazySandbox.

    Yields ``SimpleNamespace(workspace_id, org_id, org_slug, user_id,
    sandbox, lazy)``. Cleanup removes the workspace (org remains to avoid
    cascade issues across tests; it has no persisted data outside the ws).

    ``fake_opensandbox`` is required so sandbox.create never calls a real
    provider — this fixture is about skill-sync state, not sandbox lifecycle.
    """
    del fake_opensandbox  # consumed to activate monkeypatch

    from cubeplex.auth.users import _slugify_org_name
    from cubeplex.models import Organization, Workspace

    suffix = secrets.token_hex(4)
    org_name = f"sync-e2e-{suffix}"
    org_slug = _slugify_org_name(org_name)

    async with session_factory() as setup_session:
        org = Organization(name=org_name, slug=org_slug)
        setup_session.add(org)
        await setup_session.flush()
        ws = Workspace(name=f"sync-ws-{suffix}", org_id=org.id)
        setup_session.add(ws)
        await setup_session.flush()
        org_id: str = org.id
        ws_id: str = ws.id
        # Create a real User row so user_sandboxes.user_id FK is satisfied.
        from fastapi_users.db import SQLAlchemyUserDatabase
        from fastapi_users.schemas import BaseUserCreate

        from cubeplex.auth.users import UserManager

        email = f"sync-e2e-{suffix}@example.com"
        password = secrets.token_urlsafe(12)
        user_db = SQLAlchemyUserDatabase(setup_session, User)
        manager_user = UserManager(user_db)
        user_obj = await manager_user.create(
            BaseUserCreate(email=email, password=password), safe=False
        )
        user_id: str = str(user_obj.id)
        await setup_session.commit()

    mgr = SandboxManager(session_factory, _SYNC_ENCRYPTION_BACKEND)
    lazy = LazySandbox(
        manager=mgr,
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
    )

    # Materialise the underlying sandbox (first execute triggers create).
    await lazy.execute("true")

    try:
        yield SimpleNamespace(
            workspace_id=ws_id,
            org_id=org_id,
            org_slug=org_slug,
            user_id=user_id,
            # Exposed so event-recording tests can pass user_sandbox_id to
            # UserSandboxSyncEventService.record without touching the DB.
            user_sandbox_id=lazy._user_sandbox_id,
            sandbox=lazy._sandbox,  # underlying Sandbox handle
            lazy=lazy,
        )
    finally:
        with contextlib.suppress(Exception):
            await lazy.close()
        async with session_factory() as cleanup_session:
            # Circular FK between user_sandboxes and user_sandbox_sync_events:
            #   user_sandbox_sync_events.user_sandbox_id → user_sandboxes.id
            #   user_sandboxes.last_skill_sync_event_id → user_sandbox_sync_events.id
            # Break the cycle by NULLing the back-pointer first, then delete
            # both tables, then clean up skill installs before dropping the ws.
            from sqlalchemy import delete, update

            from cubeplex.models.skill import OrgSkillInstall
            from cubeplex.models.user_sandbox import UserSandbox
            from cubeplex.models.user_sandbox_sync_event import UserSandboxSyncEvent

            await cleanup_session.execute(
                update(UserSandbox)
                .where(UserSandbox.workspace_id == ws_id)  # type: ignore[arg-type]
                .values(last_skill_sync_event_id=None)
            )
            await cleanup_session.execute(
                delete(UserSandboxSyncEvent).where(
                    UserSandboxSyncEvent.workspace_id == ws_id  # type: ignore[arg-type]
                )
            )
            await cleanup_session.execute(
                delete(UserSandbox).where(
                    UserSandbox.workspace_id == ws_id  # type: ignore[arg-type]
                )
            )
            await cleanup_session.execute(
                delete(OrgSkillInstall).where(
                    OrgSkillInstall.workspace_id == ws_id  # type: ignore[arg-type]
                )
            )
            ws_row = await cleanup_session.get(Workspace, ws_id)
            if ws_row is not None:
                await cleanup_session.delete(ws_row)
            await cleanup_session.commit()


@pytest_asyncio.fixture
async def admin_client_and_sandbox(
    fresh_workspace_and_sandbox: SimpleNamespace,
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[httpx.AsyncClient, SimpleNamespace]]:
    """Admin HTTP client authenticated in the same org as ``fresh_workspace_and_sandbox``.

    Seeds a fresh admin user whose ``OrganizationMembership`` points to
    ``ns.org_id``.  This ensures ``resolve_current_org_id`` returns the sandbox's
    org so the admin route's org-scope filter matches the seeded rows.

    Yields ``(client, ns)`` where ``ns`` is the ``fresh_workspace_and_sandbox``
    namespace.
    """
    ns = fresh_workspace_and_sandbox
    suffix = secrets.token_hex(4)
    email = f"admin-obs-{suffix}@example.com"
    password = secrets.token_urlsafe(12)

    from sqlalchemy import delete as sa_delete

    from cubeplex.models import OrganizationMembership, OrgRole
    from cubeplex.repositories import OrganizationMembershipRepository

    async with session_factory() as session:
        user_db_inst = SQLAlchemyUserDatabase(session, User)
        mgr = UserManager(user_db_inst)
        user_obj = await mgr.create(BaseUserCreate(email=email, password=password), safe=False)
        user_id = str(user_obj.id)

        # Grant workspace membership.
        mem_repo = MembershipRepository(session)
        await mem_repo.grant(user_id=user_id, workspace_id=ns.workspace_id, role=Role.ADMIN)

        # Grant org-level OWNER so require_org_admin passes.
        await OrganizationMembershipRepository(session).grant(
            user_id=user_id, org_id=ns.org_id, role=OrgRole.OWNER
        )

        # Strip any bootstrap-created personal org memberships; on_after_register
        # fires and creates a personal org — remove it so resolve_current_org_id
        # picks ns.org_id (highest priority) rather than the personal org.
        await session.execute(
            sa_delete(OrganizationMembership).where(
                OrganizationMembership.user_id == user_id,  # type: ignore[arg-type]
                OrganizationMembership.org_id != ns.org_id,  # type: ignore[arg-type]
            )
        )
        await session.commit()

    app = _make_memory_test_app()
    app.state.deployment_mode = "multi_tenant"
    try:
        async with _lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                await _login_and_attach(c, email, password)
                yield c, ns
    finally:
        # Remove admin user's workspace membership so fresh_workspace_and_sandbox's
        # cleanup can delete the workspace without FK violations.
        from sqlalchemy import delete as sa_delete2

        from cubeplex.models import Membership as MembershipModel
        from cubeplex.models import OrganizationMembership

        async with session_factory() as cleanup:
            await cleanup.execute(
                sa_delete2(MembershipModel).where(
                    MembershipModel.workspace_id == ns.workspace_id,  # type: ignore[arg-type]
                    MembershipModel.user_id == user_id,  # type: ignore[arg-type]
                )
            )
            await cleanup.execute(
                sa_delete2(OrganizationMembership).where(
                    OrganizationMembership.user_id == user_id,  # type: ignore[arg-type]
                )
            )
            await cleanup.commit()


def _minimal_skill_zip(slug: str, version: str = "1.0.0") -> bytes:
    """Return a minimal valid SKILL.md zip for the given slug."""
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "SKILL.md",
            f"---\nname: {slug}\nversion: {version}\ndescription: probe skill\n---\n# {slug}\n",
        )
    return buf.getvalue()


async def install_skill_for_workspace(
    session: AsyncSession,
    *,
    org_id: str,
    org_slug: str,
    workspace_id: str,
    user_id: str,
    slug: str = "probe-1",
) -> str:
    """Publish + workspace-install a minimal skill; return the skill_id.

    The OrgSkillInstall row is workspace-private (workspace_id set), so it
    only appears in this workspace's enabled-skills list.  Object storage
    upload is real (rustfs).
    """
    from cubeplex.skills.cache import SkillCache
    from cubeplex.skills.service import SkillPublishService

    cache_dir = Path(tempfile.mkdtemp())
    publisher = SkillPublishService(session=session, cache=SkillCache(cache_root=cache_dir))
    sv = await publisher.publish_from_zip(
        org_id=org_id,
        org_slug=org_slug,
        actor_user_id=user_id,
        zip_bytes=_minimal_skill_zip(slug),
        workspace_id=workspace_id,
    )
    return sv.skill_id


async def uninstall_skill_for_workspace(
    session: AsyncSession,
    *,
    workspace_id: str,
    org_id: str,
    skill_id: str,
) -> None:
    """Remove the OrgSkillInstall row scoping skill_id to workspace_id.

    Also deletes the SkillVersion and Skill rows so tests that iterate
    the full catalog don't see stale rows from prior runs.
    """
    from sqlalchemy import delete, select

    from cubeplex.models.skill import OrgSkillInstall, Skill, SkillVersion

    await session.execute(
        delete(OrgSkillInstall).where(
            OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
            OrgSkillInstall.workspace_id == workspace_id,  # type: ignore[arg-type]
            OrgSkillInstall.skill_id == skill_id,  # type: ignore[arg-type]
        )
    )
    sv_ids = (
        (
            await session.execute(
                select(SkillVersion.id).where(
                    SkillVersion.skill_id == skill_id  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    if sv_ids:
        await session.execute(
            delete(SkillVersion).where(SkillVersion.id.in_(sv_ids))  # type: ignore[attr-defined]
        )
    await session.execute(
        delete(Skill).where(Skill.id == skill_id)  # type: ignore[arg-type]
    )
    await session.commit()
