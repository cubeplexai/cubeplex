"""Unit tests for the GET ``/api/v1/oauth/mcp/callback`` route.

The route is unauthenticated by design: state is HMAC-bound and the
ticket cookie cross-checks the actor before any DB write. We exercise
the full route with httpx ``ASGITransport`` and DI overrides for redis,
the callback handler, and the state store. AS calls go through
``httpx.MockTransport`` (no network).

Behaviors covered:

- Happy path: 302 to ``frontend_base_url/oauth/mcp/return?install_id=...&status=ok``
  and the ticket cookie is stripped on the response.
- Cookie missing → ``reason=callback_ticket_missing``.
- Cookie set but redis ticket row missing → ``reason=callback_ticket_expired``.
- State invalid (handler raises) → ``reason=state_invalid``.
- PKCE missing → ``reason=pkce_missing``.
- Token endpoint 4xx → ``reason=token_exchange_failed``.
- Actor mismatch (state.actor != ticket.actor) → ``reason=invalid_server_state``.
- Cookie is stripped on every response (success + error).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import fakeredis.aioredis
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.api.routes.v1 import mcp_oauth as mcp_oauth_routes
from cubebox.credentials.encryption import FernetBackend
from cubebox.mcp._constants import server_url_hash
from cubebox.mcp.dependencies import (
    get_oauth_callback_handler,
    get_oauth_state_store,
    get_redis,
)
from cubebox.mcp.oauth.callback import (
    PKCE_REDIS_KEY_PREFIX,
    OAuthCallbackHandler,
)
from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubebox.mcp.oauth.start import (
    CALLBACK_TICKET_COOKIE_NAME,
    CALLBACK_TICKET_REDIS_KEY_PREFIX,
)
from cubebox.mcp.oauth.state import OAuthStateStore
from cubebox.models import MCPServer
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.mcp import MCPServerRepository, UserMCPCredentialRepository
from cubebox.services.credential import CredentialService

ORG_ID = "org-test"
USER_ID = "user-test"
SERVER_URL = "https://mcp.example.com"
TOKEN_ENDPOINT = "https://auth.example.com/oauth/token"
REDIRECT_URI = "https://app.example.com/api/v1/oauth/mcp/callback"
STATE_SECRET = b"unit-test-state-secret-bytes!!!!"
FRONTEND_BASE_URL = "http://localhost:3000"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db_session:
        yield db_session
    await engine.dispose()


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield fake
    finally:
        await fake.flushall()


@pytest.fixture
def encryption_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


def _well_known_resource_url(base: str) -> str:
    return f"{base.rstrip('/')}/.well-known/oauth-protected-resource"


def _well_known_as_url(issuer: str) -> str:
    return f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server"


def _metadata_responses() -> dict[str, httpx.Response]:
    return {
        _well_known_resource_url(SERVER_URL): httpx.Response(
            200,
            json={
                "resource": SERVER_URL,
                "authorization_servers": ["https://auth.example.com"],
            },
        ),
        _well_known_as_url("https://auth.example.com"): httpx.Response(
            200,
            json={
                "issuer": "https://auth.example.com",
                "authorization_endpoint": "https://auth.example.com/oauth/authorize",
                "token_endpoint": TOKEN_ENDPOINT,
            },
        ),
    }


class _Handler:
    def __init__(
        self,
        baseline: dict[str, httpx.Response],
        token_responses: list[httpx.Response],
    ) -> None:
        self._baseline = baseline
        self._token_responses = list(token_responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == TOKEN_ENDPOINT:
            if not self._token_responses:
                return httpx.Response(500, text="no scripted response left")
            return self._token_responses.pop(0)
        if url in self._baseline:
            return self._baseline[url]
        return httpx.Response(404, json={"error": "not_found"})


def _credential_service_factory(
    session: AsyncSession,
    backend: FernetBackend,
) -> Callable[[str | None, str | None], CredentialService]:
    def _factory(org_id: str | None, actor_user_id: str | None) -> CredentialService:
        repo = CredentialRepository(session, org_id=org_id)
        return CredentialService(repo, backend, org_id=org_id, actor_user_id=actor_user_id)

    return _factory


async def _seed_oauth_install(
    session: AsyncSession,
    *,
    credential_scope: str,
    owner_workspace_id: str | None,
) -> MCPServer:
    server_repo = MCPServerRepository(session, org_id=ORG_ID)
    return await server_repo.add(
        MCPServer(
            org_id=ORG_ID,
            owner_workspace_id=owner_workspace_id,
            name=f"oauth-install-{credential_scope}",
            server_url=SERVER_URL,
            server_url_hash=server_url_hash(SERVER_URL),
            transport="streamable_http",
            auth_method="oauth",
            credential_scope=credential_scope,
            credential_id=None,
            oauth_client_config={"client_id": "client-abc"},
            authed=False,
            created_by_user_id=USER_ID,
        )
    )


def _build_app(
    *,
    fake_redis: fakeredis.aioredis.FakeRedis,
    callback_handler: OAuthCallbackHandler,
    state_store: OAuthStateStore,
) -> FastAPI:
    app = FastAPI()
    app.include_router(mcp_oauth_routes.oauth_callback_router, prefix="/api/v1")

    async def _override_redis() -> Any:
        return fake_redis

    async def _override_handler() -> Any:
        return callback_handler

    async def _override_state_store() -> Any:
        return state_store

    app.dependency_overrides[get_redis] = _override_redis
    app.dependency_overrides[get_oauth_callback_handler] = _override_handler
    app.dependency_overrides[get_oauth_state_store] = _override_state_store
    return app


def _make_callback_handler(
    *,
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
    http: httpx.AsyncClient,
    state_store: OAuthStateStore,
) -> OAuthCallbackHandler:
    return OAuthCallbackHandler(
        http_client=http,
        redis=fake_redis,
        state_store=state_store,
        metadata=OAuthMetadataDiscovery(http),
        encryption_backend=encryption_backend,
        credential_service_factory=_credential_service_factory(session, encryption_backend),
        # Callback runs unauthenticated → repos use org_id=None.
        server_repo=MCPServerRepository(session, org_id=None),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=None),
        redirect_uri=REDIRECT_URI,
    )


def _parse_redirect(response: httpx.Response) -> dict[str, list[str]]:
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    parsed = urlparse(location)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        f"{FRONTEND_BASE_URL}/oauth/mcp/return"
    )
    return parse_qs(parsed.query)


def _assert_cookie_stripped(response: httpx.Response) -> None:
    """`delete_cookie` writes a Set-Cookie with Max-Age=0 (or expires in past)."""
    set_cookie_headers = [v for k, v in response.headers.multi_items() if k.lower() == "set-cookie"]
    matching = [h for h in set_cookie_headers if CALLBACK_TICKET_COOKIE_NAME in h]
    assert matching, f"no Set-Cookie for {CALLBACK_TICKET_COOKIE_NAME}; got {set_cookie_headers}"
    # Either Max-Age=0 or expires in 1970.
    h = matching[0]
    assert "Max-Age=0" in h or "Expires=Thu, 01 Jan 1970" in h, h


async def _hit_callback(
    app: FastAPI,
    *,
    code: str | None = None,
    state: str,
    error: str | None = None,
    cookies: dict[str, str] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        cookies=cookies or {},
    ) as client:
        params: dict[str, str] = {"state": state}
        if code is not None:
            params["code"] = code
        if error is not None:
            params["error"] = error
        return await client.get(
            "/api/v1/oauth/mcp/callback",
            params=params,
        )


# ---------------- Happy path ---------------- #


async def test_callback_happy_path_redirects_ok_and_strips_cookie(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server = await _seed_oauth_install(session, credential_scope="org", owner_workspace_id=None)
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    state = await state_store.issue(install_id=server.id, actor_user_id=USER_ID)
    await fake_redis.set(PKCE_REDIS_KEY_PREFIX + server.id, "verifier-1")
    ticket = "ticket-happy"
    await fake_redis.set(CALLBACK_TICKET_REDIS_KEY_PREFIX + ticket, USER_ID, ex=600)

    handler = _Handler(
        _metadata_responses(),
        token_responses=[
            httpx.Response(
                200,
                json={
                    "access_token": "fresh-access",
                    "refresh_token": "fresh-refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        ],
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_callback_handler(
        session=session,
        fake_redis=fake_redis,
        encryption_backend=encryption_backend,
        http=http,
        state_store=state_store,
    )
    app = _build_app(
        fake_redis=fake_redis,
        callback_handler=cb_handler,
        state_store=state_store,
    )

    async def _fake_discover(
        srv: MCPServer, *, credential_or_token: str | None
    ) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
        return True, [], None

    try:
        with patch("cubebox.mcp.runtime.discover_tools", _fake_discover):
            response = await _hit_callback(
                app, code="auth-code", state=state, cookies={CALLBACK_TICKET_COOKIE_NAME: ticket}
            )
    finally:
        await http.aclose()

    qs = _parse_redirect(response)
    assert qs["status"] == ["ok"]
    assert qs["install_id"] == [server.id]
    _assert_cookie_stripped(response)

    # Ticket key consumed.
    assert await fake_redis.get(CALLBACK_TICKET_REDIS_KEY_PREFIX + ticket) is None


# ---------------- Cookie missing ---------------- #


async def test_callback_missing_cookie_returns_ticket_missing(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    handler = _Handler(_metadata_responses(), token_responses=[])
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_callback_handler(
        session=session,
        fake_redis=fake_redis,
        encryption_backend=encryption_backend,
        http=http,
        state_store=state_store,
    )
    app = _build_app(
        fake_redis=fake_redis,
        callback_handler=cb_handler,
        state_store=state_store,
    )
    try:
        response = await _hit_callback(app, code="x", state="anything", cookies=None)
    finally:
        await http.aclose()

    qs = _parse_redirect(response)
    assert qs["status"] == ["error"]
    assert qs["reason"] == ["callback_ticket_missing"]
    _assert_cookie_stripped(response)


# ---------------- Cookie set but redis ticket row missing ---------------- #


async def test_callback_expired_cookie_returns_ticket_expired(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    handler = _Handler(_metadata_responses(), token_responses=[])
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_callback_handler(
        session=session,
        fake_redis=fake_redis,
        encryption_backend=encryption_backend,
        http=http,
        state_store=state_store,
    )
    app = _build_app(
        fake_redis=fake_redis,
        callback_handler=cb_handler,
        state_store=state_store,
    )
    try:
        response = await _hit_callback(
            app,
            code="x",
            state="anything",
            cookies={CALLBACK_TICKET_COOKIE_NAME: "ticket-not-in-redis"},
        )
    finally:
        await http.aclose()

    qs = _parse_redirect(response)
    assert qs["reason"] == ["callback_ticket_expired"]
    _assert_cookie_stripped(response)


# ---------------- State invalid ---------------- #


async def test_callback_invalid_state_returns_state_invalid(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    ticket = "ticket-state-bad"
    await fake_redis.set(CALLBACK_TICKET_REDIS_KEY_PREFIX + ticket, USER_ID, ex=600)

    handler = _Handler(_metadata_responses(), token_responses=[])
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_callback_handler(
        session=session,
        fake_redis=fake_redis,
        encryption_backend=encryption_backend,
        http=http,
        state_store=state_store,
    )
    app = _build_app(
        fake_redis=fake_redis,
        callback_handler=cb_handler,
        state_store=state_store,
    )
    try:
        response = await _hit_callback(
            app,
            code="x",
            state="garbage.token",
            cookies={CALLBACK_TICKET_COOKIE_NAME: ticket},
        )
    finally:
        await http.aclose()

    qs = _parse_redirect(response)
    assert qs["reason"] == ["state_invalid"]
    _assert_cookie_stripped(response)


# ---------------- PKCE missing ---------------- #


async def test_callback_pkce_missing_returns_pkce_missing(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server = await _seed_oauth_install(session, credential_scope="org", owner_workspace_id=None)
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    state = await state_store.issue(install_id=server.id, actor_user_id=USER_ID)
    # No PKCE row in redis on purpose.
    ticket = "ticket-pkce-miss"
    await fake_redis.set(CALLBACK_TICKET_REDIS_KEY_PREFIX + ticket, USER_ID, ex=600)

    handler = _Handler(_metadata_responses(), token_responses=[])
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_callback_handler(
        session=session,
        fake_redis=fake_redis,
        encryption_backend=encryption_backend,
        http=http,
        state_store=state_store,
    )
    app = _build_app(
        fake_redis=fake_redis,
        callback_handler=cb_handler,
        state_store=state_store,
    )
    try:
        response = await _hit_callback(
            app, code="x", state=state, cookies={CALLBACK_TICKET_COOKIE_NAME: ticket}
        )
    finally:
        await http.aclose()

    qs = _parse_redirect(response)
    assert qs["reason"] == ["pkce_missing"]
    _assert_cookie_stripped(response)


# ---------------- Token endpoint 4xx ---------------- #


async def test_callback_token_exchange_failed_returns_token_exchange_failed(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server = await _seed_oauth_install(session, credential_scope="org", owner_workspace_id=None)
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    state = await state_store.issue(install_id=server.id, actor_user_id=USER_ID)
    await fake_redis.set(PKCE_REDIS_KEY_PREFIX + server.id, "verifier-bad")
    ticket = "ticket-tokfail"
    await fake_redis.set(CALLBACK_TICKET_REDIS_KEY_PREFIX + ticket, USER_ID, ex=600)

    handler = _Handler(
        _metadata_responses(),
        token_responses=[
            httpx.Response(400, json={"error": "invalid_grant", "error_description": "expired"})
        ],
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_callback_handler(
        session=session,
        fake_redis=fake_redis,
        encryption_backend=encryption_backend,
        http=http,
        state_store=state_store,
    )
    app = _build_app(
        fake_redis=fake_redis,
        callback_handler=cb_handler,
        state_store=state_store,
    )
    try:
        response = await _hit_callback(
            app, code="bad", state=state, cookies={CALLBACK_TICKET_COOKIE_NAME: ticket}
        )
    finally:
        await http.aclose()

    qs = _parse_redirect(response)
    assert qs["reason"] == ["token_exchange_failed"]
    _assert_cookie_stripped(response)


# ---------------- Actor mismatch ---------------- #


async def test_callback_actor_mismatch_returns_invalid_server_state(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    server = await _seed_oauth_install(session, credential_scope="org", owner_workspace_id=None)
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    # State signed for USER_ID, but ticket bound to a different user.
    state = await state_store.issue(install_id=server.id, actor_user_id=USER_ID)
    await fake_redis.set(PKCE_REDIS_KEY_PREFIX + server.id, "verifier-x")
    ticket = "ticket-mismatch"
    await fake_redis.set(CALLBACK_TICKET_REDIS_KEY_PREFIX + ticket, "different-user", ex=600)

    handler = _Handler(_metadata_responses(), token_responses=[])
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_callback_handler(
        session=session,
        fake_redis=fake_redis,
        encryption_backend=encryption_backend,
        http=http,
        state_store=state_store,
    )
    app = _build_app(
        fake_redis=fake_redis,
        callback_handler=cb_handler,
        state_store=state_store,
    )
    try:
        response = await _hit_callback(
            app, code="x", state=state, cookies={CALLBACK_TICKET_COOKIE_NAME: ticket}
        )
    finally:
        await http.aclose()

    qs = _parse_redirect(response)
    assert qs["reason"] == ["invalid_server_state"]
    _assert_cookie_stripped(response)


# ---------------- AS-side error (no code) ---------------- #


async def test_callback_as_error_access_denied_redirects_user_denied(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    """RFC 6749 §4.1.2.1: AS may redirect with ?error= and no code.

    User clicking Deny on the consent screen produces ``error=access_denied``.
    The route must accept the request (code is optional) and redirect to
    the frontend return page with ``reason=user_denied``, consuming and
    stripping the ticket cookie either way.
    """
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    handler = _Handler(_metadata_responses(), token_responses=[])
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_callback_handler(
        session=session,
        fake_redis=fake_redis,
        encryption_backend=encryption_backend,
        http=http,
        state_store=state_store,
    )
    app = _build_app(
        fake_redis=fake_redis,
        callback_handler=cb_handler,
        state_store=state_store,
    )
    ticket = "ticket-deny"
    await fake_redis.set(CALLBACK_TICKET_REDIS_KEY_PREFIX + ticket, USER_ID, ex=600)

    try:
        response = await _hit_callback(
            app,
            code=None,
            state="any-state-the-handler-wont-consume",
            error="access_denied",
            cookies={CALLBACK_TICKET_COOKIE_NAME: ticket},
        )
    finally:
        await http.aclose()

    qs = _parse_redirect(response)
    assert qs["status"] == ["error"]
    assert qs["reason"] == ["user_denied"]
    _assert_cookie_stripped(response)
    # Ticket key consumed even though we never reached the handler.
    assert await fake_redis.get(CALLBACK_TICKET_REDIS_KEY_PREFIX + ticket) is None


async def test_callback_as_error_other_redirects_provider_error(
    session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
    encryption_backend: FernetBackend,
) -> None:
    """Non-access_denied AS errors (server_error, invalid_scope, etc.) → provider_error."""
    state_store = OAuthStateStore(redis=fake_redis, secret_key=STATE_SECRET, ttl_seconds=300)
    handler = _Handler(_metadata_responses(), token_responses=[])
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cb_handler = _make_callback_handler(
        session=session,
        fake_redis=fake_redis,
        encryption_backend=encryption_backend,
        http=http,
        state_store=state_store,
    )
    app = _build_app(
        fake_redis=fake_redis,
        callback_handler=cb_handler,
        state_store=state_store,
    )
    ticket = "ticket-server-error"
    await fake_redis.set(CALLBACK_TICKET_REDIS_KEY_PREFIX + ticket, USER_ID, ex=600)

    try:
        response = await _hit_callback(
            app,
            code=None,
            state="ignored",
            error="server_error",
            cookies={CALLBACK_TICKET_COOKIE_NAME: ticket},
        )
    finally:
        await http.aclose()

    qs = _parse_redirect(response)
    assert qs["status"] == ["error"]
    assert qs["reason"] == ["provider_error"]
    _assert_cookie_stripped(response)
