"""User identity middleware.

Assigns an anonymous user_id to every request via:
1. X-User-ID header (explicit, e.g. from API clients)
2. cubebox_user_id cookie (implicit, for browser sessions)
3. Auto-generated UUID7 (first visit)

The user_id is stored in request.state.user_id and persisted as a cookie.
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from uuid_utils import uuid7

COOKIE_NAME = "cubebox_user_id"
COOKIE_MAX_AGE = 86400 * 365  # 1 year


class UserIdentityMiddleware(BaseHTTPMiddleware):
    """Middleware that ensures every request has a user_id."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Priority: header > cookie > generate
        user_id = request.headers.get("X-User-ID")
        if not user_id:
            user_id = request.cookies.get(COOKIE_NAME)

        is_new = False
        if not user_id:
            user_id = str(uuid7())
            is_new = True

        request.state.user_id = user_id

        response = await call_next(request)

        # Set cookie so the ID persists across browser sessions
        if is_new:
            response.set_cookie(
                COOKIE_NAME,
                user_id,
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
            )

        return response
