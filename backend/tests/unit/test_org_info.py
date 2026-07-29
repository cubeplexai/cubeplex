"""Unit tests for the public org lookup the login page calls.

Stays in the default lane: without it a deployment with no optional package
could not tell the login page whether to offer an SSO button.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException

from cubeplex.api.routes.v1.org_info import get_org_info
from cubeplex.models import Organization, SSOConnection, User

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def org_with_oidc_sso(
    sso_session: Any, make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]]
) -> tuple[Organization, SSOConnection]:
    org, _user = await make_org_with_user(email="admin@acme.com")
    conn = SSOConnection(
        org_id=org.id,
        protocol="oidc",
        display_name="Acme OIDC",
        status="active",
        provisioning="auto",
        config={
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/jwks",
            "client_id": "cubeplex-client",
        },
    )
    sso_session.add(conn)
    await sso_session.commit()
    await sso_session.refresh(conn)
    return org, conn


async def test_org_info_returns_sso_enabled(
    sso_session: Any,
    org_with_oidc_sso: tuple[Organization, SSOConnection],
) -> None:
    org, _ = org_with_oidc_sso
    resp = await get_org_info(org.slug, sso_session)
    assert resp.org_name == org.name
    assert resp.sso_enabled is True
    assert resp.sso_protocol == "oidc"


async def test_org_info_no_sso(
    sso_session: Any,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    org, _ = await make_org_with_user(email="solo@example.com")
    resp = await get_org_info(org.slug, sso_session)
    assert resp.sso_enabled is False
    assert resp.sso_protocol is None


async def test_org_info_404_for_unknown_slug(sso_session: Any) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_org_info("does-not-exist", sso_session)
    assert exc_info.value.status_code == 404


# --- initiate ---------------------------------------------------------------
