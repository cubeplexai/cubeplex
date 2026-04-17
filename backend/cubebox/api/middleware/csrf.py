"""CSRF double-submit-cookie middleware.

On every safe request (GET/HEAD/OPTIONS), set a `cubebox_csrf` cookie if absent.
On every mutating request (POST/PUT/PATCH/DELETE), require the cookie value to match
the `X-CSRF-Token` header. Skip enforcement if there is no auth cookie present (so
unauthenticated routes still work).
"""

import json
import secrets
from http.cookies import SimpleCookie

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cubebox.config import config

CSRF_COOKIE = "cubebox_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CSRFMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.auth_cookie = config.get("auth.cookie_name", "cubebox_auth")
        self.cookie_secure = config.get("auth.cookie_secure", False)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"].upper()
        cookies = _parse_cookies(scope["headers"])
        has_auth = self.auth_cookie in cookies
        csrf_cookie = cookies.get(CSRF_COOKIE)

        if method not in SAFE_METHODS and has_auth:
            csrf_header = _get_header(scope["headers"], CSRF_HEADER.encode())
            if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
                await _send_403(send, "CSRF token missing or mismatched")
                return

        if csrf_cookie is None and method in SAFE_METHODS:
            new_token = secrets.token_urlsafe(32)

            async def send_with_csrf(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    cookie: SimpleCookie = SimpleCookie()
                    cookie[CSRF_COOKIE] = new_token
                    cookie[CSRF_COOKIE]["path"] = "/"
                    cookie[CSRF_COOKIE]["samesite"] = "Lax"
                    cookie[CSRF_COOKIE]["max-age"] = "86400"
                    if self.cookie_secure:
                        cookie[CSRF_COOKIE]["secure"] = True
                    headers.append("set-cookie", cookie[CSRF_COOKIE].OutputString())
                await send(message)

            await self.app(scope, receive, send_with_csrf)
            return

        await self.app(scope, receive, send)


def _parse_cookies(headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers:
        if k == b"cookie":
            cookie: SimpleCookie = SimpleCookie(v.decode("latin-1"))
            for name, morsel in cookie.items():
                out[name] = morsel.value
    return out


def _get_header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for k, v in headers:
        if k == name:
            return v.decode("latin-1")
    return None


async def _send_403(send: Send, message: str) -> None:
    body = json.dumps({"error_code": "CSRF_FORBIDDEN", "message": message}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
