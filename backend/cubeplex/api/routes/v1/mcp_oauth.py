"""MCP OAuth callback route.

Delegates to :class:`cubeplex.mcp.oauth.OAuthCallbackHandler`, which decodes
the HMAC-signed state token, exchanges the auth code for tokens, and writes
the grant row. The canonical authorized signal is ``grant.grant_status == 'valid'``.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import RedirectResponse

from cubeplex.config import config
from cubeplex.mcp.dependencies import get_oauth_callback_handler
from cubeplex.mcp.oauth import OAuthCallbackHandler

# Cookie name preserved so a future implementation can reuse the
# infrastructure without breaking the cookie ticket contract.
CALLBACK_TICKET_COOKIE_NAME = "cubeplex_mcp_oauth_ticket"
_CALLBACK_COOKIE_PATH = "/api/v1/oauth/mcp/callback"

oauth_callback_router = APIRouter(prefix="/oauth/mcp", tags=["mcp-oauth-callback"])


def _frontend_return_url(frontend_origin: str | None = None) -> str:
    base = (
        frontend_origin.rstrip("/")
        if frontend_origin
        else str(config.get("frontend_base_url", "http://localhost:3000")).rstrip("/")
    )
    return f"{base}/oauth/mcp/return"


def _strip_ticket_cookie(response: Response) -> None:
    response.delete_cookie(
        key=CALLBACK_TICKET_COOKIE_NAME,
        path=_CALLBACK_COOKIE_PATH,
    )


@oauth_callback_router.get("/callback", include_in_schema=True)
async def oauth_callback(
    handler: Annotated[OAuthCallbackHandler, Depends(get_oauth_callback_handler)],
    state: Annotated[str, Query()],
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Four-layer OAuth callback: AS redirect → grant write → return-page redirect."""
    result = await handler.handle_callback(state=state, code=code, error=error)
    params: dict[str, str] = {
        "status": result.status,
        "state": result.state,
        "connector_id": result.connector_id,
    }
    if result.reason:
        params["reason"] = result.reason
    url = f"{_frontend_return_url(result.frontend_origin)}?{urlencode(params)}"
    response = RedirectResponse(url=url, status_code=302)
    _strip_ticket_cookie(response)
    return response
