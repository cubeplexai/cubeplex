"""E2E auth tests: register, login, logout, duplicate email, me-requires-auth."""

import secrets
import time

import httpx
import jwt
import pytest
from sqlalchemy import select

import cubeplex.db as cubeplex_db
from cubeplex.api.middleware.rate_limit import limiter
from cubeplex.models import Conversation, Role, SteeringMessage, User, Workspace
from tests.e2e.conftest import (
    DEFAULT_TEST_EMAIL,
    _auth_cookie_name,
    _ensure_default_user_and_membership,
    _lifespan_context,
    _login_and_attach,
    _make_isolated_user,
)
from tests.e2e.helpers import csrf_cookie_name

pytestmark = pytest.mark.e2e

SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60


@pytest.mark.asyncio
async def test_delete_account_removes_steering_sent_to_another_users_conversation() -> None:
    await _ensure_default_user_and_membership()
    app, email, password, workspace_id = await _make_isolated_user(Role.MEMBER)
    app.state.deployment_mode = "multi_tenant"
    steer_id: str
    conversation_id: str
    deleting_user_id: str

    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login_and_attach(client, email, password)
            async with cubeplex_db.async_session_maker() as session:
                deleting_user = (
                    await session.execute(select(User).where(User.email == email))
                ).scalar_one()
                other_user = (
                    await session.execute(select(User).where(User.email == DEFAULT_TEST_EMAIL))
                ).scalar_one()
                workspace = await session.get(Workspace, workspace_id)
                assert workspace is not None
                conversation = Conversation(
                    org_id=workspace.org_id,
                    workspace_id=workspace.id,
                    creator_user_id=other_user.id,
                    title="survives steering sender deletion",
                )
                session.add(conversation)
                await session.flush()
                steering = SteeringMessage(
                    org_id=workspace.org_id,
                    workspace_id=workspace.id,
                    conversation_id=conversation.id,
                    run_id="run-account-delete",
                    client_steer_id="steer-account-delete",
                    content="personal steering text",
                    sender_user_id=deleting_user.id,
                    hitl_question_id="question-account-delete",
                )
                session.add(steering)
                await session.commit()
                steer_id = steering.id
                conversation_id = conversation.id
                deleting_user_id = deleting_user.id

            response = await client.post(
                "/api/v1/auth/delete-account",
                json={"password": password},
            )
            assert response.status_code == 200, response.text

            async with cubeplex_db.async_session_maker() as session:
                assert await session.get(SteeringMessage, steer_id) is None
                assert await session.get(User, deleting_user_id) is None
                persisted_conversation = await session.get(Conversation, conversation_id)
                assert persisted_conversation is not None
                await session.delete(persisted_conversation)
                await session.commit()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the shared slowapi limiter between tests.

    Without this, register/login rate limits (3/min, 5/min) accumulate across
    the test run and cause spurious 429s — all requests share the same
    ASGI-transport remote address.
    """
    limiter.reset()
    yield
    limiter.reset()


@pytest.mark.asyncio
async def test_register_and_login_sets_cookie(unauthenticated_memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorsebatterystaple"

    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    assert r.status_code == 201, r.text

    before_login = int(time.time())
    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": pw}
    )
    after_login = int(time.time())
    assert r.status_code == 204
    assert _auth_cookie_name() in unauthenticated_memory_client.cookies
    auth_cookie = next(
        cookie
        for cookie in r.headers.get_list("set-cookie")
        if cookie.startswith(f"{_auth_cookie_name()}=")
    )
    assert f"Max-Age={SESSION_LIFETIME_SECONDS}" in auth_cookie
    token = unauthenticated_memory_client.cookies.get(_auth_cookie_name())
    assert token is not None
    claims = jwt.decode(token, options={"verify_signature": False})
    assert before_login + SESSION_LIFETIME_SECONDS <= claims["exp"]
    assert claims["exp"] <= after_login + SESSION_LIFETIME_SECONDS

    me = await unauthenticated_memory_client.get("/api/v1/auth/me")
    csrf_cookie = next(
        cookie
        for cookie in me.headers.get_list("set-cookie")
        if cookie.startswith(f"{csrf_cookie_name()}=")
    )
    assert f"Max-Age={SESSION_LIFETIME_SECONDS}" in csrf_cookie


@pytest.mark.asyncio
async def test_login_wrong_password_fails(unauthenticated_memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": "right-password-1"}
    )
    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": "wrong-password"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_register_duplicate_email_fails(unauthenticated_memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorse"
    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    assert r.status_code == 201
    r2 = await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_logout_clears_cookie(unauthenticated_memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorse"
    await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    await unauthenticated_memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": pw}
    )
    # Seed CSRF cookie via a safe GET (logout is a mutating request on an
    # authenticated session, so CSRF middleware requires the double-submit token).
    await unauthenticated_memory_client.get("/api/v1/auth/me")
    csrf = unauthenticated_memory_client.cookies.get(csrf_cookie_name()) or ""
    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 204
    r2 = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert r2.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_auth(unauthenticated_memory_client):
    r = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_default_language(unauthenticated_memory_client):
    """Test that GET /auth/me returns language field with default value."""
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorsebatterystaple"

    # Register and login
    await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    await unauthenticated_memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": pw}
    )

    # Fresh user should have default language "en"
    resp = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["language"] == "en"


@pytest.mark.asyncio
async def test_patch_me_updates_language(unauthenticated_memory_client):
    """Test that PATCH /auth/me updates and persists language."""
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorsebatterystaple"

    # Register and login
    await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    await unauthenticated_memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": pw}
    )

    # Seed CSRF cookie via a safe GET.
    await unauthenticated_memory_client.get("/api/v1/auth/me")
    csrf = unauthenticated_memory_client.cookies.get(csrf_cookie_name()) or ""

    # Update language
    resp = await unauthenticated_memory_client.patch(
        "/api/v1/auth/me",
        json={"language": "zh"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    assert resp.json()["language"] == "zh"

    # Verify persisted
    me = await unauthenticated_memory_client.get("/api/v1/auth/me")
    assert me.json()["language"] == "zh"


@pytest.mark.asyncio
async def test_patch_me_rejects_invalid_language(unauthenticated_memory_client):
    """Test that PATCH /auth/me rejects invalid language values."""
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorsebatterystaple"

    # Register and login
    await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    await unauthenticated_memory_client.post(
        "/api/v1/auth/login", data={"username": email, "password": pw}
    )

    # Seed CSRF cookie via a safe GET.
    await unauthenticated_memory_client.get("/api/v1/auth/me")
    csrf = unauthenticated_memory_client.cookies.get(csrf_cookie_name()) or ""

    # Attempt to set invalid language
    resp = await unauthenticated_memory_client.patch(
        "/api/v1/auth/me",
        json={"language": "ja"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422  # Pydantic Literal validation


@pytest.mark.asyncio
async def test_login_error_is_localized_zh(
    unauthenticated_memory_client,
) -> None:
    """Login error is localized to Chinese when Accept-Language is zh."""
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/login",
        data={"username": "nobody@example.com", "password": "wrong"},
        headers={"Accept-Language": "zh-CN,zh;q=0.9"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "邮箱或密码错误"


@pytest.mark.asyncio
async def test_login_error_is_localized_en(
    unauthenticated_memory_client,
) -> None:
    """Login error is in English when Accept-Language is en."""
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/login",
        data={"username": "nobody@example.com", "password": "wrong"},
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid email or password"
