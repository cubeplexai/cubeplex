from fastapi import APIRouter

from cubeplex.plugins import AuthProvider
from cubeplex.plugins.defaults.auth import DefaultAuthProvider


def test_default_auth_provider_satisfies_protocol() -> None:
    p = DefaultAuthProvider()
    assert isinstance(p, AuthProvider)


def test_default_auth_provider_returns_routers() -> None:
    """DefaultAuthProvider returns CE's existing auth router(s).

    Adapted from plan: CE uses a single composite router at
    cubeplex.api.routes.v1.auth that bundles register/login/me/logout with
    rate limits, CSRF, and register bootstrap — not fastapi-users' 3-router
    default. The contract is `list[APIRouter]`; count is an implementation
    detail.
    """
    p = DefaultAuthProvider()
    routers = p.get_auth_routers()
    assert isinstance(routers, list)
    assert len(routers) >= 1
    assert all(isinstance(r, APIRouter) for r in routers)
