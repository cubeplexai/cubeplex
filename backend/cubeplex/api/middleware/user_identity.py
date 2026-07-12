"""Pure ASGI user identity middleware.

Assigns an anonymous user_id to every request via:
1. X-User-ID header (explicit, e.g. from API clients)
2. cubeplex_user_id cookie (implicit, for browser sessions)
3. Auto-generated UUID7 (first visit)

The user_id is stored in scope["state"]["user_id"] (accessible as request.state.user_id)
and persisted as a cookie.

Uses pure ASGI instead of BaseHTTPMiddleware to avoid task-boundary
cancellation issues that cause SQLAlchemy async connection pool leaks.
"""

from http.cookies import SimpleCookie

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uuid_utils import uuid7

COOKIE_NAME = "cubeplex_user_id"
COOKIE_MAX_AGE = 86400 * 365  # 1 year


class UserIdentityMiddleware:
    """Middleware that ensures every request has a user_id."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # scope["headers"] is a list of (name, value) byte tuples
        raw_headers: list[tuple[bytes, bytes]] = scope["headers"]

        # Priority: header > cookie > generate
        user_id = _get_header(raw_headers, b"x-user-id")
        if not user_id:
            user_id = _get_cookie(raw_headers, COOKIE_NAME)

        is_new = False
        if not user_id:
            user_id = str(uuid7())
            is_new = True

        # Make user_id available as request.state.user_id
        scope.setdefault("state", {})["user_id"] = user_id

        if not is_new:
            await self.app(scope, receive, send)
            return

        # Intercept the first response to inject the Set-Cookie header
        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                cookie: SimpleCookie = SimpleCookie()
                cookie[COOKIE_NAME] = user_id
                cookie[COOKIE_NAME]["max-age"] = str(COOKIE_MAX_AGE)
                cookie[COOKIE_NAME]["httponly"] = True
                cookie[COOKIE_NAME]["samesite"] = "Lax"
                cookie[COOKIE_NAME]["path"] = "/"
                response_headers.append("set-cookie", cookie[COOKIE_NAME].OutputString())
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def _get_header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    """Extract a header value from raw ASGI headers."""
    for key, value in headers:
        if key == name:
            return value.decode("latin-1")
    return None


def _get_cookie(headers: list[tuple[bytes, bytes]], cookie_name: str) -> str | None:
    """Extract a cookie value from raw ASGI headers."""
    for key, value in headers:
        if key == b"cookie":
            cookie: SimpleCookie = SimpleCookie(value.decode("latin-1"))
            morsel = cookie.get(cookie_name)
            if morsel:
                return morsel.value
    return None
