"""CSRF double-submit-cookie middleware.

On every safe request (GET/HEAD/OPTIONS), set the CSRF cookie (default
`cubebox_csrf`, configurable via `auth.csrf_cookie_name`) if absent. On every
mutating request (POST/PUT/PATCH/DELETE), require the cookie value to match the
`X-CSRF-Token` header. Skip enforcement if there is no auth cookie present (so
unauthenticated routes still work).
"""

import json
import secrets
from http.cookies import SimpleCookie

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cubebox.config import config

CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CSRFMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.auth_cookie = config.get("auth.cookie_name", "cubebox_auth")
        self.csrf_cookie = config.get("auth.csrf_cookie_name", "cubebox_csrf")
        self.cookie_secure = config.get("auth.cookie_secure", False)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"].upper()
        cookies = _parse_cookies(scope["headers"])
        has_auth = self.auth_cookie in cookies
        csrf_cookie = cookies.get(self.csrf_cookie)
        # Bearer-authed requests (API keys) bypass CSRF: there is no cookie to
        # replay so the attack the double-submit cookie defends against does
        # not apply. The Bearer token is the authentication.
        has_bearer = _has_bearer_auth(scope["headers"])

        if method not in SAFE_METHODS and has_auth and not has_bearer:
            csrf_header = _get_header(scope["headers"], CSRF_HEADER.encode())
            if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
                await _send_403(send, "CSRF token missing or mismatched")
                return

        if csrf_cookie is None and method in SAFE_METHODS:
            new_token = secrets.token_urlsafe(32)
            csrf_name = self.csrf_cookie
            cookie_secure = self.cookie_secure

            async def send_with_csrf(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    cookie: SimpleCookie = SimpleCookie()
                    cookie[csrf_name] = new_token
                    cookie[csrf_name]["path"] = "/"
                    cookie[csrf_name]["samesite"] = "Lax"
                    cookie[csrf_name]["max-age"] = "86400"
                    if cookie_secure:
                        cookie[csrf_name]["secure"] = True
                    headers.append("set-cookie", cookie[csrf_name].OutputString())
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


def _has_bearer_auth(headers: list[tuple[bytes, bytes]]) -> bool:
    raw = _get_header(headers, b"authorization")
    if not raw:
        return False
    scheme, _, token = raw.partition(" ")
    return scheme.lower() == "bearer" and bool(token.strip())


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
