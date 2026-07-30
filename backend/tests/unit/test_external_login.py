"""Unit tests for finishing an external-provider login.

Both Google social login (open source) and enterprise SSO (licensed) end here,
so these stay in the default lane: they guard a forced-SSO security control that
has to hold with no optional package installed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from fastapi import HTTPException
from starlette.requests import Request

from cubeplex.auth.external_login import (
    enforce_forced_sso_for_user,
    login_and_redirect,
)
from cubeplex.models import (
    Membership,
    Organization,
    Role,
    SSOConnection,
    User,
    Workspace,
)

pytestmark = pytest.mark.asyncio


def _make_request(redis: fakeredis.aioredis.FakeRedis) -> Request:
    """Minimal Starlette Request. ``login_and_redirect`` ignores it, but keeping
    the same construction as the licensed handler tests means the two lanes stay
    independent rather than sharing a fixture module across the split."""

    class _State:
        pass

    class _App:
        state = _State()

    app = _App()
    app.state.redis = redis  # type: ignore[attr-defined]
    return Request({"type": "http", "headers": [], "app": app})


@pytest_asyncio.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.flushall()
    await redis.aclose()


async def test_enforce_forced_sso_blocks_when_allowed_org_is_none(
    sso_session: Any,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """A user in an org with active SSO must not log in via a path that
    doesn't go through that org (e.g. social login)."""
    org, user = await make_org_with_user(email="member@corp.com")
    sso_session.add(
        SSOConnection(
            org_id=org.id,
            protocol="oidc",
            display_name="C",
            status="active",
            provisioning="auto",
            config={},
        )
    )
    await sso_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await enforce_forced_sso_for_user(sso_session, user, allowed_org_id=None)
    assert exc_info.value.status_code == 403
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail["code"] == "sso_required"


async def test_enforce_forced_sso_blocks_when_allowed_org_is_different(
    sso_session: Any,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    org, user = await make_org_with_user(email="m@corp.com")
    sso_session.add(
        SSOConnection(
            org_id=org.id,
            protocol="oidc",
            display_name="C",
            status="active",
            provisioning="auto",
            config={},
        )
    )
    await sso_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await enforce_forced_sso_for_user(sso_session, user, allowed_org_id="org-some-other")
    assert exc_info.value.status_code == 403


async def test_enforce_forced_sso_passes_when_allowed_org_matches(
    sso_session: Any,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    org, user = await make_org_with_user(email="m2@corp.com")
    sso_session.add(
        SSOConnection(
            org_id=org.id,
            protocol="oidc",
            display_name="C",
            status="active",
            provisioning="auto",
            config={},
        )
    )
    await sso_session.commit()

    # Should NOT raise
    await enforce_forced_sso_for_user(sso_session, user, allowed_org_id=org.id)


async def test_enforce_forced_sso_ignores_testing_status(
    sso_session: Any,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """`testing` status connections must not trigger the forced-SSO block —
    only `active` does."""
    org, user = await make_org_with_user(email="t@corp.com")
    sso_session.add(
        SSOConnection(
            org_id=org.id,
            protocol="oidc",
            display_name="C",
            status="testing",
            provisioning="auto",
            config={},
        )
    )
    await sso_session.commit()

    await enforce_forced_sso_for_user(sso_session, user, allowed_org_id=None)


# --- _login_and_redirect: workspace pick by membership ---------------------


async def test_login_and_redirect_picks_workspace_user_belongs_to(
    sso_session: Any,
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    """The SSO redirect must land on a workspace where the user has a
    Membership — not just any workspace in the org."""
    org, user = await make_org_with_user(email="ws-user@example.com")

    # Workspace the user is NOT a member of — must not be picked.
    other_ws = Workspace(org_id=org.id, name="Other WS")
    sso_session.add(other_ws)
    await sso_session.flush()

    # Workspace the user IS a member of.
    my_ws = Workspace(org_id=org.id, name="My WS")
    sso_session.add(my_ws)
    await sso_session.flush()
    sso_session.add(Membership(user_id=user.id, workspace_id=my_ws.id, role=Role.MEMBER))
    await sso_session.commit()

    request = _make_request(fake_redis)
    resp = await login_and_redirect(request, sso_session, user)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert f"/w/{my_ws.id}" in location
    assert other_ws.id not in location


async def test_login_and_redirect_falls_back_to_base_when_no_membership(
    sso_session: Any,
    fake_redis: fakeredis.aioredis.FakeRedis,
    make_org_with_user: Callable[..., Awaitable[tuple[Organization, User]]],
) -> None:
    _, user = await make_org_with_user(email="no-ws@example.com")
    request = _make_request(fake_redis)
    resp = await login_and_redirect(request, sso_session, user)
    assert resp.status_code == 302
    # No workspace was a membership target → redirect to base URL.
    assert "/w/" not in resp.headers["location"]


async def test_unserviceable_sso_log_separates_active_from_testing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Active and testing have different consequences, so the log must not merge
    them: enforce_forced_sso_for_user refuses passwords for active only, so a
    testing org's users can still sign in. Telling an operator otherwise sends
    them to disable a connection that is costing them nothing.
    """
    import logging
    from unittest.mock import AsyncMock, MagicMock, patch

    from cubeplex.auth.external_login import report_unserviceable_sso

    rows = [("org-a", "active"), ("org-t", "testing")]
    result = MagicMock()
    result.all.return_value = rows
    session = AsyncMock()
    session.execute.return_value = result

    with patch("cubeplex.plugins.ee.is_ee_installed", return_value=False):
        with caplog.at_level(logging.ERROR, logger="cubeplex.auth.external_login"):
            affected = await report_unserviceable_sso(session)

    assert affected == ["org-a", "org-t"]

    active_line = next(rec.getMessage() for rec in caplog.records if "org-a" in rec.getMessage())
    testing_line = next(rec.getMessage() for rec in caplog.records if "org-t" in rec.getMessage())
    assert active_line is not testing_line, "the two statuses must be reported separately"

    assert "cannot sign in at all" in active_line
    assert "disable-sso" in active_line, "the locked-out case needs the recovery command"
    assert "unaffected" in testing_line
    assert "disable-sso" not in testing_line, "no urgent action for a testing connection"


async def test_unserviceable_sso_log_truncates_a_long_org_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """All ids come back; the log names a few and counts the rest."""
    import logging
    from unittest.mock import AsyncMock, MagicMock, patch

    from cubeplex.auth.external_login import report_unserviceable_sso

    result = MagicMock()
    result.all.return_value = [(f"org-{i}", "active") for i in range(7)]
    session = AsyncMock()
    session.execute.return_value = result

    with patch("cubeplex.plugins.ee.is_ee_installed", return_value=False):
        with caplog.at_level(logging.ERROR, logger="cubeplex.auth.external_login"):
            affected = await report_unserviceable_sso(session)

    assert affected == sorted(f"org-{i}" for i in range(7))
    assert "and 2 more" in caplog.text, "7 orgs, 5 named: the count must not be dropped"
    assert "cubeplex-ee" in caplog.text, "operator needs to know which package is missing"
