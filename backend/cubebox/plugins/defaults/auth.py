"""CE default AuthProvider: wraps CE's existing cookie/JWT auth router.

The AuthProvider Protocol contract accepts only a Request, so the CE
default opens its own database session inline to look up the authenticated
user. This keeps the plugin contract DB-agnostic — EE plugins (SAML/OIDC)
may not need a session at all and should not be forced to accept one.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, Request
from sqlalchemy import select

import cubebox.db as _db
from cubebox.models import ApiKey, User

# Debounce: avoid a DB write on every Bearer-authed request. Writes are
# skipped if the stored ``last_used_at`` is within this window.
_LAST_USED_DEBOUNCE = timedelta(seconds=60)


class DefaultAuthProvider:
    """CE default: Bearer API key OR cookie-based JWT via fastapi-users."""

    async def authenticate(self, request: Request) -> User | None:
        """Resolve the active user via Bearer API key first, then cookie JWT.

        Opens a short-lived DB session for the lookup. This is the
        auth-time session; the route's own Depends(get_session) still
        opens a separate session for the business logic.

        Heavy fastapi-users imports are deferred to call-time to avoid
        triggering the fastapi_users.db import chain during plugin loading
        (the plugin test suite instantiates DefaultAuthProvider without a
        running DB, so module-level imports of fastapi-users internals would
        fail there).
        """
        bearer_user = await self._authenticate_bearer(request)
        if bearer_user is not None:
            return bearer_user

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

    async def _authenticate_bearer(self, request: Request) -> User | None:
        """If a Bearer token resolves to a known API key, return its owner."""
        auth_header = request.headers.get("authorization") or request.headers.get(
            "Authorization"
        )
        if not auth_header:
            return None
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None

        hashed = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
        async with _db.async_session_maker() as session:
            stmt = select(ApiKey).where(ApiKey.hashed_key == hashed)  # type: ignore[arg-type]
            api_key = (await session.execute(stmt)).scalar_one_or_none()
            if api_key is None:
                return None
            user_stmt = select(User).where(User.id == api_key.user_id)  # type: ignore[arg-type]
            user = (await session.execute(user_stmt)).scalar_one_or_none()
            if user is None or not user.is_active:
                return None
            await self._maybe_touch_last_used(session, api_key)
            return user

    @staticmethod
    async def _maybe_touch_last_used(session: Any, api_key: ApiKey) -> None:
        now = datetime.now(UTC)
        previous = api_key.last_used_at
        if previous is not None and now - previous < _LAST_USED_DEBOUNCE:
            return
        api_key.last_used_at = now
        await session.commit()

    def get_auth_routers(self) -> list[APIRouter]:
        from cubebox.api.routes.v1.auth import router as auth_router

        return [auth_router]
