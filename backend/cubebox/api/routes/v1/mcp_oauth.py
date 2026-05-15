"""MCP OAuth callback route.

Four-layer OAuth start routes live as 501 stubs in ``admin_mcp.py`` and
``ws_mcp.py``. The callback handshake (AS authorize_code → token exchange →
``MCPCredentialGrant`` upsert) lands in plan Task 6.

Until then, the callback endpoint short-circuits to the frontend return
page with ``status=error&reason=callback_not_wired`` so the browser flow
fails fast instead of hanging.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse

from cubebox.config import config

# Cookie name preserved so a future Task-6 implementation can reuse the
# infrastructure without breaking the cookie ticket contract.
CALLBACK_TICKET_COOKIE_NAME = "cubebox_mcp_oauth_ticket"
_CALLBACK_COOKIE_PATH = "/api/v1/oauth/mcp/callback"

oauth_callback_router = APIRouter(prefix="/oauth/mcp", tags=["mcp-oauth-callback"])


def _frontend_return_url() -> str:
    base = str(config.get("frontend_base_url", "http://localhost:3000")).rstrip("/")
    return f"{base}/oauth/mcp/return"


def _strip_ticket_cookie(response: Response) -> None:
    response.delete_cookie(
        key=CALLBACK_TICKET_COOKIE_NAME,
        path=_CALLBACK_COOKIE_PATH,
    )


@oauth_callback_router.get("/callback", include_in_schema=True)
async def oauth_callback(
    request: Request,  # noqa: ARG001 — present so handler signature can evolve
    state: Annotated[str, Query()],
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,  # noqa: ARG001
) -> RedirectResponse:
    """Stub: four-layer OAuth callback handler lands in plan Task 6."""
    _ = state, code  # avoid unused-arg warnings
    params: dict[str, str] = {
        "install_id": "",
        "status": "error",
        "reason": "callback_not_wired",
    }
    url = f"{_frontend_return_url()}?{urlencode(params)}"
    response = RedirectResponse(url=url, status_code=302)
    _strip_ticket_cookie(response)
    return response
