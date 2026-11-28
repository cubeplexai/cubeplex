"""CSRF middleware security contracts."""

import httpx
import pytest
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from cubeplex.api.middleware.csrf import CSRFMiddleware
from cubeplex.config import config


async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    await Response(status_code=204)(scope, receive, send)


@pytest.mark.asyncio
async def test_invalid_bearer_cannot_bypass_cookie_csrf() -> None:
    app: ASGIApp = CSRFMiddleware(_ok_app)
    transport = httpx.ASGITransport(app=app)
    auth_cookie = str(config.get("auth.cookie_name", "cubeplex_auth"))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/mutating-route",
            cookies={auth_cookie: "valid-cookie-session"},
            headers={"Authorization": "Bearer not-a-real-key"},
        )

    assert response.status_code == 403
    assert response.json()["error_code"] == "CSRF_FORBIDDEN"


_CALL_PATH = "/api/v1/ws/ws-1/conversations/conv-1/artifacts/art-1/call/chat"


@pytest.mark.asyncio
async def test_read_only_artifact_call_is_exempt() -> None:
    """Artifact HTML cannot read the configurable CSRF cookie name."""
    app: ASGIApp = CSRFMiddleware(_ok_app)
    transport = httpx.ASGITransport(app=app)
    auth_cookie = str(config.get("auth.cookie_name", "cubeplex_auth"))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_CALL_PATH, cookies={auth_cookie: "valid-cookie-session"})

    assert response.status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", f"{_CALL_PATH}/extra"),
        ("POST", "/api/v1/ws/ws-1/conversations/conv-1/artifacts/art-1/share-token"),
        ("DELETE", _CALL_PATH),
    ],
)
async def test_csrf_exemption_is_narrow(method: str, path: str) -> None:
    app: ASGIApp = CSRFMiddleware(_ok_app)
    transport = httpx.ASGITransport(app=app)
    auth_cookie = str(config.get("auth.cookie_name", "cubeplex_auth"))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, path, cookies={auth_cookie: "valid-cookie-session"})

    assert response.status_code == 403
    assert response.json()["error_code"] == "CSRF_FORBIDDEN"
