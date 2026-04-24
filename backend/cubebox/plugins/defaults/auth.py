"""CE default AuthProvider: wraps CE's existing cookie/JWT auth router.

Unlike the plan's original sketch, CE does NOT use fastapi-users' default
get_auth_router/get_register_router/get_users_router — CE has hand-rolled
handlers in cubebox.api.routes.v1.auth that bundle rate limiting (slowapi),
CSRF cookie issuance on login, and the Organization/Workspace/Membership
bootstrap on register. We expose that composite router as-is so the plugin
contract doesn't alter production behavior.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.jwt import auth_backend
from cubebox.auth.users import get_user_manager
from cubebox.models import User


class DefaultAuthProvider:
    """CE default: cookie-based JWT via fastapi-users + custom route layer."""

    async def authenticate(self, request: Request, session: AsyncSession) -> User | None:
        """Authenticate user by extracting and validating JWT from cookies.

        Mimics fastapi-users.current_user(active=True, optional=True) by:
        1. Extracting token from cookie via transport.scheme
        2. Validating token and getting user via strategy.read_token
        3. Returning user if active, None otherwise
        """
        # Extract token from cookies using the backend's transport
        token: str | None = None
        try:
            token = await auth_backend.transport.scheme(request)  # type: ignore[operator]
        except Exception:
            # Transport returns None if token missing or invalid
            pass

        if token is None:
            return None

        # Get user manager to look up user from token
        from cubebox.auth.db import get_user_db

        user_db_inst = await get_user_db(session).__anext__()
        async for user_mgr in get_user_manager(user_db_inst):
            # Validate token and get user
            strategy: Any = auth_backend.get_strategy()
            user = cast(User | None, await strategy.read_token(token, user_mgr))
            if user and user.is_active:
                return user
            return None
        return None

    def get_auth_routers(self) -> list[APIRouter]:
        # Import here to avoid circular import at module load (auth routes
        # depend on the auth module which, in Task 16, will depend on the
        # plugin registry that may want to resolve this class at startup).
        from cubebox.api.routes.v1.auth import router as auth_router

        return [auth_router]
