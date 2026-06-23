"""Unit tests for POST /api/v1/im/link/confirm."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from cubebox.api.routes.v1.im_link import router
from cubebox.im.link import sign_link_token
from cubebox.models.user import User

_SECRET = "test-jwt-secret"


def _make_user(email: str = "chris@example.com", user_id: str = "usr_1") -> User:
    return User(id=user_id, email=email, hashed_password="x")


def _make_app(user: User | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    if user is not None:
        from cubebox.auth.dependencies import current_active_user

        app.dependency_overrides[current_active_user] = lambda: user

    # The confirm endpoint depends on EncryptionBackend so it can build a
    # Feishu client for the post-confirm IM notice. These unit tests never
    # exercise that path (platform="discord", chat_id=""), but FastAPI
    # still resolves the dependency on every request — stub it.
    from cubebox.credentials.dependencies import get_encryption_backend

    app.dependency_overrides[get_encryption_backend] = lambda: None  # type: ignore[return-value]

    return app


def _sign(
    email: str = "chris@example.com",
    im_user_id: str = "discord_42",
    account_id: str = "imca_abc",
    workspace_id: str = "ws_xyz",
    platform: str = "discord",
) -> str:
    return sign_link_token(
        im_user_id=im_user_id,
        email=email,
        account_id=account_id,
        workspace_id=workspace_id,
        platform=platform,
        secret=_SECRET,
    )


@pytest.mark.anyio
async def test_email_mismatch_rejected() -> None:
    user = _make_user(email="other@example.com")
    app = _make_app(user)
    token = _sign(email="chris@example.com")
    with patch("cubebox.api.routes.v1.im_link._get_jwt_secret", return_value=_SECRET):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v1/im/link/confirm", json={"token": token})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "email_mismatch"


@pytest.mark.anyio
async def test_not_workspace_member_rejected() -> None:
    user = _make_user(email="chris@example.com")
    app = _make_app(user)
    token = _sign(email="chris@example.com")
    with (
        patch("cubebox.api.routes.v1.im_link._get_jwt_secret", return_value=_SECRET),
        patch(
            "cubebox.api.routes.v1.im_link._check_membership",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v1/im/link/confirm", json={"token": token})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "not_member"


@pytest.mark.anyio
async def test_success_creates_link() -> None:
    user = _make_user(email="chris@example.com")
    app = _make_app(user)
    token = _sign(email="chris@example.com")
    with (
        patch("cubebox.api.routes.v1.im_link._get_jwt_secret", return_value=_SECRET),
        patch(
            "cubebox.api.routes.v1.im_link._check_membership",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "cubebox.api.routes.v1.im_link._upsert_identity_link",
            new_callable=AsyncMock,
        ) as mock_upsert,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v1/im/link/confirm", json={"token": token})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_upsert.assert_awaited_once()


@pytest.mark.anyio
async def test_invalid_token_rejected() -> None:
    user = _make_user()
    app = _make_app(user)
    with patch("cubebox.api.routes.v1.im_link._get_jwt_secret", return_value=_SECRET):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v1/im/link/confirm", json={"token": "garbage"})
    assert resp.status_code == 400
