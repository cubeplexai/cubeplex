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
