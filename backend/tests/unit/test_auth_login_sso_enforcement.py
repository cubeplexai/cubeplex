"""Unit tests for SSO enforcement on password login.

Drive the route handler ``login`` directly: a SQLite ``sso_session`` plus
a stubbed UserManager / Strategy / OAuth2PasswordRequestForm — this isolates
the SSO-enforcement branch from fastapi-users' real auth machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Response
from fastapi_users.exceptions import UserNotExists
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from cubeplex.api.routes.v1.auth import login
from cubeplex.models import (
    Organization,
    OrganizationMembership,
    OrgRole,
    SSOConnection,
    User,
)

pytestmark = pytest.mark.asyncio


@dataclass
class _Creds:
    """Minimal stand-in for OAuth2PasswordRequestForm."""

    username: str
    password: str


def _make_request() -> Request:
    app = MagicMock()
    # slowapi reads request.scope["path"] and looks up app.state.limiter;
    # also needs a client IP for the keyfunc.
    return Request(
        {
            "type": "http",
            "headers": [],
            "method": "POST",
            "path": "/api/v1/auth/login",
            "client": ("127.0.0.1", 1234),
            "app": app,
        }
    )


class _UserManager:
    """Stub UserManager: returns the configured user (or raises) without
    touching fastapi-users or password hashing."""

    def __init__(self, *, user: User | None, raise_not_exists: bool = False) -> None:
        self._user = user
        self._raise = raise_not_exists

    async def authenticate(self, _credentials: Any) -> User | None:
        if self._raise:
            raise UserNotExists
        return self._user


class _Strategy:
    """Stub auth strategy: never reached when SSO blocks login; otherwise
    used by ``auth_backend.login`` (we don't reach there in these tests)."""


async def _make_user(sso_session: AsyncSession, *, email: str) -> User:
    user = User(
        email=email,
        hashed_password="not-a-real-hash",
        display_name=email.split("@", 1)[0],
        is_active=True,
        is_verified=True,
    )
    sso_session.add(user)
    await sso_session.flush()
    return user


async def _add_org_with_user(
    sso_session: AsyncSession,
    *,
    email: str,
    slug: str,
    role: OrgRole = OrgRole.MEMBER,
) -> tuple[Organization, User]:
    from cubeplex.repositories import OrganizationRepository

    org = await OrganizationRepository(sso_session).create(name=f"Org {slug}", slug=slug)
    user = await _make_user(sso_session, email=email)
    sso_session.add(OrganizationMembership(user_id=user.id, org_id=org.id, role=role))
    await sso_session.commit()
    return org, user


async def _call_login(
    *,
    sso_session: AsyncSession,
    user: User | None,
    raise_not_exists: bool = False,
) -> Response:
    return await login(
        request=_make_request(),
        credentials=_Creds(username="u@example.com", password="x"),  # type: ignore[arg-type]
        user_manager=_UserManager(user=user, raise_not_exists=raise_not_exists),  # type: ignore[arg-type]
        strategy=_Strategy(),  # type: ignore[arg-type]
        session=sso_session,
        locale="en",
    )


# --- happy path -------------------------------------------------------------


async def test_login_succeeds_when_user_has_no_active_sso(
    sso_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user in an org without an SSOConnection row should pass the
    enforcement check and proceed to JWT issuance."""
    _org, user = await _add_org_with_user(sso_session, email="plain@example.com", slug="plain-org")

    sentinel = Response(content="ok")

    async def _fake_login(_strategy: Any, _user: User) -> Response:
        return sentinel

    monkeypatch.setattr("cubeplex.api.routes.v1.auth.auth_backend.login", _fake_login)
    result = await _call_login(sso_session=sso_session, user=user)
    assert result is sentinel


# --- the block -------------------------------------------------------------


async def test_login_blocked_when_active_sso_present(
    sso_session: AsyncSession,
) -> None:
    org, user = await _add_org_with_user(sso_session, email="blocked@example.com", slug="acme")
    sso_session.add(
        SSOConnection(
            org_id=org.id,
            protocol="oidc",
            display_name="Acme OIDC",
            status="active",
            provisioning="auto",
            config={},
        )
    )
    await sso_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await _call_login(sso_session=sso_session, user=user)
    assert exc_info.value.status_code == 403
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "sso_required"
    assert detail["login_url"] == f"/login/{org.slug}"
    assert detail["message"]  # localized text present


# --- testing-mode does NOT block (lockout-prevention guard) -----------------


async def test_login_not_blocked_when_sso_status_is_testing(
    sso_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admins validating a new SSO connection must still be able to log in
    with password while ``status='testing'``. This is the critical
    lockout-prevention guard."""
    org, user = await _add_org_with_user(
        sso_session, email="testing@example.com", slug="acme-testing"
    )
    sso_session.add(
        SSOConnection(
            org_id=org.id,
            protocol="oidc",
            display_name="Acme OIDC (testing)",
            status="testing",
            provisioning="auto",
            config={},
        )
    )
    await sso_session.commit()

    sentinel = Response(content="ok")

    async def _fake_login(_strategy: Any, _user: User) -> Response:
        return sentinel

    monkeypatch.setattr("cubeplex.api.routes.v1.auth.auth_backend.login", _fake_login)
    result = await _call_login(sso_session=sso_session, user=user)
    assert result is sentinel


# --- bad credentials path stays the same shape ------------------------------


async def test_login_unknown_user_returns_existing_bad_credentials_shape(
    sso_session: AsyncSession,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _call_login(sso_session=sso_session, user=None, raise_not_exists=True)
    assert exc_info.value.status_code == 400
    assert isinstance(exc_info.value.detail, str)  # NOT a structured dict
    assert "sso_required" not in exc_info.value.detail


async def test_login_wrong_password_returns_existing_bad_credentials_shape(
    sso_session: AsyncSession,
) -> None:
    """authenticate() returning None (wrong password) → same 400."""
    with pytest.raises(HTTPException) as exc_info:
        await _call_login(sso_session=sso_session, user=None)
    assert exc_info.value.status_code == 400
    assert isinstance(exc_info.value.detail, str)


# --- admin path: no exception ---------------------------------------------


async def test_login_blocked_for_org_admin_too(
    sso_session: AsyncSession,
) -> None:
    """Per spec: org admins are NOT exempt from SSO enforcement."""
    org, user = await _add_org_with_user(
        sso_session,
        email="admin@example.com",
        slug="acme-admin",
        role=OrgRole.OWNER,
    )
    sso_session.add(
        SSOConnection(
            org_id=org.id,
            protocol="oidc",
            display_name="Acme OIDC",
            status="active",
            provisioning="auto",
            config={},
        )
    )
    await sso_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await _call_login(sso_session=sso_session, user=user)
    assert exc_info.value.status_code == 403
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "sso_required"
