"""CE default AuthProvider: wraps CE's existing cookie/JWT auth router.

Unlike the plan's original sketch, CE does NOT use fastapi-users' default
get_auth_router/get_register_router/get_users_router — CE has hand-rolled
handlers in cubebox.api.routes.v1.auth that bundle rate limiting (slowapi),
CSRF cookie issuance on login, and the Organization/Workspace/Membership
bootstrap on register. We expose that composite router as-is so the plugin
contract doesn't alter production behavior.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from cubebox.auth.users import fastapi_users
from cubebox.models import User


class DefaultAuthProvider:
    """CE default: cookie-based JWT via fastapi-users + custom route layer."""

    async def authenticate(self, request: Request) -> User | None:
        get_user = fastapi_users.current_user(active=True, optional=True)
        return await get_user(request)  # type: ignore[no-any-return]

    def get_auth_routers(self) -> list[APIRouter]:
        # Import here to avoid circular import at module load (auth routes
        # depend on the auth module which, in Task 16, will depend on the
        # plugin registry that may want to resolve this class at startup).
        from cubebox.api.routes.v1.auth import router as auth_router

        return [auth_router]
