"""CE default AuthProvider: wraps CE's existing cookie/JWT auth router.

The AuthProvider Protocol contract accepts only a Request, so the CE
default opens its own database session inline to look up the authenticated
user. This keeps the plugin contract DB-agnostic — EE plugins (SAML/OIDC)
may not need a session at all and should not be forced to accept one.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Request

import cubebox.db as _db
from cubebox.models import User


class DefaultAuthProvider:
    """CE default: cookie-based JWT via fastapi-users + custom route layer."""

    async def authenticate(self, request: Request) -> User | None:
        """Extract JWT from cookie and resolve it to an active User.

        Opens a short-lived DB session for the user lookup. This is the
        auth-time session; the route's own Depends(get_session) still
        opens a separate session for the business logic.

        Heavy fastapi-users imports are deferred to call-time to avoid
        triggering the fastapi_users.db import chain during plugin loading
        (the plugin test suite instantiates DefaultAuthProvider without a
        running DB, so module-level imports of fastapi-users internals would
        fail there).
        """
        from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

        from cubebox.auth.jwt import auth_backend
        from cubebox.auth.users import get_user_manager

        try:
            token = await auth_backend.transport.scheme(request)  # type: ignore[operator]
        except Exception:
            token = None
        if token is None:
            return None

        async with _db.async_session_maker() as session:
            user_db: SQLAlchemyUserDatabase[User] = SQLAlchemyUserDatabase(session, User)  # type: ignore[type-arg]
            async for user_mgr in get_user_manager(user_db):
                strategy: Any = auth_backend.get_strategy()
                user = cast(User | None, await strategy.read_token(token, user_mgr))
                if user is not None and user.is_active:
                    return user
                return None
        return None

    def get_auth_routers(self) -> list[APIRouter]:
        from cubebox.api.routes.v1.auth import router as auth_router

        return [auth_router]
