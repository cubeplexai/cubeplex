"""E2E tests for /api/v1/user/events SSE channel and mark-read endpoint.

The SSE stream test uses a real uvicorn server on an ephemeral port because
httpx's ASGITransport buffers the full response body before returning control to
the caller — it cannot interleave background publishing with `aiter_lines()`.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import cubebox.db as _cubebox_db
from cubebox.api.app import create_app
from cubebox.db.engine import _build_database_url, engine
from cubebox.db.session import get_session
from cubebox.models.user_event import UserEventType
from cubebox.repositories.user_event import UserEventRepository
from cubebox.services.user_event import PublishUserEventInput, UserEventService
from cubebox.services.user_event_bus import UserEventBus
from tests.e2e.conftest import (
    DEFAULT_TEST_EMAIL,
    DEFAULT_TEST_PASSWORD,
    _ensure_default_user_and_membership,
    _lifespan_context,
    _login_and_attach,
)
from tests.e2e.helpers import csrf_cookie_name

# ---------------------------------------------------------------------------
# Fixture: async client (ASGI transport) for non-SSE tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def user_events_client() -> AsyncIterator[tuple[httpx.AsyncClient, str, UserEventBus]]:
    """Fresh app + async client + bus reference for mark-read tests.

    Yields (client, user_id, bus).
    """
    await _ensure_default_user_and_membership()

    url = _build_database_url()
    test_engine = create_async_engine(url, poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    _cubebox_db.async_session_maker = test_session_maker

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_maker() as session:
            yield session

    from fastapi import FastAPI

    app: FastAPI = create_app()
    app.dependency_overrides[get_session] = override_get_session
    app.state.deployment_mode = "multi_tenant"

    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, DEFAULT_TEST_EMAIL, DEFAULT_TEST_PASSWORD)
            me_resp = await c.get("/api/v1/auth/me")
            me_resp.raise_for_status()
            user_id: str = me_resp.json()["id"]
            bus: UserEventBus = app.state.user_event_bus
            yield c, user_id, bus

    await engine.dispose()


# ---------------------------------------------------------------------------
# Fixture: real uvicorn server for SSE streaming tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def live_user_events_server() -> AsyncIterator[tuple[str, str, str, UserEventBus]]:
    """Spin up a real uvicorn server on an ephemeral port.

    Yields (base_url, auth_cookie_value, csrf_token, bus).
    The bus is the same instance used by the server (read from app.state after
    startup).
    """
    await _ensure_default_user_and_membership()

    url = _build_database_url()
    test_engine = create_async_engine(url, poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    _cubebox_db.async_session_maker = test_session_maker

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_maker() as session:
            yield session

    from fastapi import FastAPI

    app: FastAPI = create_app()
    app.dependency_overrides[get_session] = override_get_session
    app.state.deployment_mode = "multi_tenant"

    # Grab an ephemeral port.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)
    serve_task = asyncio.create_task(server.serve())

    # Wait until started.
    deadline = 5.0
    elapsed = 0.0
    while not server.started and elapsed < deadline:
        await asyncio.sleep(0.05)
        elapsed += 0.05

    base_url = f"http://127.0.0.1:{port}"

    # Log in to get session cookies + CSRF token.
    _csrf_cookie_name = csrf_cookie_name()
    async with httpx.AsyncClient(base_url=base_url) as setup_c:
        # get CSRF cookie first
        await setup_c.get("/api/v1/auth/me")
        csrf_val = setup_c.cookies.get(_csrf_cookie_name) or ""
        login_resp = await setup_c.post(
            "/api/v1/auth/login",
            data={"username": DEFAULT_TEST_EMAIL, "password": DEFAULT_TEST_PASSWORD},
            headers={"X-CSRF-Token": csrf_val},
        )
        assert login_resp.status_code in (200, 204), f"login failed: {login_resp.text}"
        csrf_token: str = setup_c.cookies.get(_csrf_cookie_name) or csrf_val

        # Collect all cookies to pass to the streaming client.
        cookies = dict(setup_c.cookies)

    bus: UserEventBus = app.state.user_event_bus

    try:
        yield base_url, cookies, csrf_token, bus
    finally:
        server.should_exit = True
        await serve_task
        await test_engine.dispose()

    await engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_receives_live_event(
    live_user_events_server: tuple[str, dict, str, UserEventBus],
) -> None:
    """Open SSE stream against real uvicorn, publish event, assert delivery."""
    base_url, cookies, csrf_token, bus = live_user_events_server

    pub_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    pub_session_maker = async_sessionmaker(pub_engine, class_=AsyncSession, expire_on_commit=False)

    # Discover the logged-in user_id.
    async with httpx.AsyncClient(base_url=base_url, cookies=cookies) as setup_c:
        me_resp = await setup_c.get("/api/v1/auth/me", headers={"X-CSRF-Token": csrf_token})
        me_resp.raise_for_status()
        user_id: str = me_resp.json()["id"]

    received: dict | None = None

    async def fire_event() -> None:
        await asyncio.sleep(0.3)  # let stream open and subscribe
        async with pub_session_maker() as session:
            repo = UserEventRepository(session)
            svc = UserEventService(repo=repo, bus=bus)
            await svc.publish(
                PublishUserEventInput(
                    user_id=user_id,
                    workspace_id=None,
                    type=UserEventType.MEMORY_UPDATED,
                    payload={"items": [{"op": "save", "memory_id": "mem_test_1"}]},
                )
            )
        await pub_engine.dispose()

    fire_task = asyncio.create_task(fire_event())

    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            cookies=cookies,
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
        ) as stream_c:
            async with stream_c.stream(
                "GET",
                "/api/v1/user/events",
                headers={"Accept": "text/event-stream", "X-CSRF-Token": csrf_token},
            ) as resp:
                assert resp.status_code == 200, resp.text
                assert "text/event-stream" in resp.headers.get("content-type", "")

                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        received = json.loads(line.removeprefix("data: "))
                        break
    finally:
        fire_task.cancel()
        try:
            await fire_task
        except (asyncio.CancelledError, Exception):
            pass

    assert received is not None, "expected at least one SSE data line"
    assert received["type"] == "memory_updated"
    assert received["id"].startswith("uev-")


@pytest.mark.asyncio
async def test_mark_read(
    user_events_client: tuple[httpx.AsyncClient, str, UserEventBus],
) -> None:
    """Publish an event, POST mark-read, assert 204 and DB row has read_at set."""
    client, user_id, bus = user_events_client

    pub_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    pub_session_maker = async_sessionmaker(pub_engine, class_=AsyncSession, expire_on_commit=False)

    ev_id: str
    async with pub_session_maker() as session:
        repo = UserEventRepository(session)
        svc = UserEventService(repo=repo, bus=bus)
        ev = await svc.publish(
            PublishUserEventInput(
                user_id=user_id,
                workspace_id=None,
                type=UserEventType.MEMORY_UPDATED,
                payload={"items": []},
            )
        )
        ev_id = ev.id
    await pub_engine.dispose()

    resp = await client.post(f"/api/v1/user/events/{ev_id}/read")
    assert resp.status_code == 204, resp.text

    # Verify DB state: read_at is now set.
    check_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    check_session_maker = async_sessionmaker(
        check_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with check_session_maker() as session:
        repo = UserEventRepository(session)
        rows = await repo.list_for_user(user_id, since_id=None, limit=50)
        target = next((r for r in rows if r.id == ev_id), None)
        assert target is not None
        assert target.read_at is not None, "read_at should be set after mark-read"
    await check_engine.dispose()


@pytest.mark.asyncio
async def test_mark_read_wrong_user_returns_404(
    user_events_client: tuple[httpx.AsyncClient, str, UserEventBus],
) -> None:
    """mark-read for a non-existent event id returns 404."""
    client, _user_id, _bus = user_events_client
    resp = await client.post("/api/v1/user/events/uev-does-not-exist/read")
    assert resp.status_code == 404
