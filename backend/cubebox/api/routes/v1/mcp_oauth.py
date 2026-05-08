"""MCP OAuth routes — Phase 5 of the catalog + OAuth design.

Three routers (mounted with ``/api/v1`` prefix in ``api/app.py``):

- ``oauth_admin_router`` — ``POST /admin/mcp/installs/{install_id}/oauth/start``
  for org admins re-keying an org-wide install.
- ``oauth_member_router`` — ``POST /ws/{workspace_id}/mcp/installs/{install_id}/oauth/start``
  for the workspace user who created a workspace-private install.
- ``oauth_callback_router`` — ``GET /oauth/mcp/callback``. No auth: state is
  HMAC-bound and the cookie ticket cross-checks the actor before any DB
  write. Always returns a 302 to the frontend return page; never reveals
  raw error details.

Both ``/oauth/start`` paths set the ``cubebox_mcp_oauth_ticket`` cookie
(HttpOnly, Secure-when-cookie_secure, SameSite=Lax,
``Path=/api/v1/oauth/mcp/callback``). The callback consumes the cookie
and strips it on every response (success or failure).

Per spec §11: error reasons are short machine codes only; raw tokens,
codes, state values, and PKCE verifiers must NEVER be logged.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import RedirectResponse
from loguru import logger
from redis.asyncio import Redis

from cubebox.api.schemas.mcp import MCPOAuthStartIn, MCPOAuthStartOut
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.config import config
from cubebox.mcp.dependencies import (
    get_admin_request_context,
    get_oauth_callback_handler,
    get_oauth_start_service_admin,
    get_oauth_start_service_member,
    get_redis,
)
from cubebox.mcp.exceptions import (
    MCPCatalogConnectorNotFound,
    MCPServerNotFound,
    OAuthCallbackError,
    OAuthInvalidServerState,
    OAuthPKCEMissing,
    OAuthStateExpired,
    OAuthStateInvalid,
)
from cubebox.mcp.oauth.callback import OAuthCallbackHandler
from cubebox.mcp.oauth.start import (
    CALLBACK_TICKET_COOKIE_NAME,
    CALLBACK_TICKET_REDIS_KEY_PREFIX,
    CALLBACK_TICKET_TTL_SECONDS,
    OAuthStartService,
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

oauth_admin_router = APIRouter(prefix="/admin/mcp", tags=["admin-mcp-oauth"])
oauth_member_router = APIRouter(prefix="/ws/{workspace_id}/mcp", tags=["workspace-mcp-oauth"])
oauth_callback_router = APIRouter(prefix="/oauth/mcp", tags=["mcp-oauth-callback"])

# Path the callback cookie is scoped to. MUST match the GET endpoint path
# below (the FastAPI prefix + route).
_CALLBACK_COOKIE_PATH = "/api/v1/oauth/mcp/callback"


def _cookie_secure() -> bool:
    return bool(config.get("auth.cookie_secure", False))


def _set_ticket_cookie(response: Response, ticket: str) -> None:
    response.set_cookie(
        key=CALLBACK_TICKET_COOKIE_NAME,
        value=ticket,
        max_age=CALLBACK_TICKET_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path=_CALLBACK_COOKIE_PATH,
    )


def _strip_ticket_cookie(response: Response) -> None:
    response.delete_cookie(
        key=CALLBACK_TICKET_COOKIE_NAME,
        path=_CALLBACK_COOKIE_PATH,
    )


def _map_start_exception(exc: Exception) -> HTTPException:
    """Map ``OAuthStartService`` failures to HTTP responses."""
    if isinstance(exc, MCPServerNotFound):
        return HTTPException(
            404,
            detail={
                "code": "mcp_oauth.install_not_found",
                "message": "MCP install not found.",
            },
        )
    if isinstance(exc, MCPCatalogConnectorNotFound):
        return HTTPException(
            404,
            detail={
                "code": "mcp_oauth.connector_not_found",
                "message": "Catalog connector for install was not found.",
            },
        )
    if isinstance(exc, OAuthInvalidServerState):
        return HTTPException(
            400,
            detail={
                "code": "mcp_oauth.invalid_server_state",
                "message": "OAuth start cannot proceed for this install.",
            },
        )
    return HTTPException(
        500,
        detail={
            "code": "mcp_oauth.internal_error",
            "message": "Unexpected internal error during OAuth start.",
        },
    )


# ---------------------------------------------------------------------------
# 5.1 — POST /api/v1/admin/mcp/installs/{install_id}/oauth/start
# ---------------------------------------------------------------------------


@oauth_admin_router.post(
    "/installs/{install_id}/oauth/start",
    response_model=MCPOAuthStartOut,
)
async def admin_oauth_start(
    install_id: str,
    body: MCPOAuthStartIn,  # noqa: ARG001 — kept for OpenAPI clarity
    response: Response,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    svc: Annotated[OAuthStartService, Depends(get_oauth_start_service_admin)],
) -> MCPOAuthStartOut:
    """Org admin starts OAuth for an org-wide install."""
    try:
        result = await svc.start(install_id=install_id, actor_user_id=ctx.user.id)
    except (
        MCPServerNotFound,
        MCPCatalogConnectorNotFound,
        OAuthInvalidServerState,
    ) as exc:
        raise _map_start_exception(exc) from exc

    _set_ticket_cookie(response, result.cookie_value)
    return MCPOAuthStartOut(authorize_url=result.authorize_url, state=result.state)


# ---------------------------------------------------------------------------
# 5.2 — POST /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/oauth/start
# ---------------------------------------------------------------------------


@oauth_member_router.post(
    "/installs/{install_id}/oauth/start",
    response_model=MCPOAuthStartOut,
)
async def workspace_oauth_start(
    install_id: str,
    body: MCPOAuthStartIn,  # noqa: ARG001 — kept for OpenAPI clarity
    response: Response,
    workspace_id: Annotated[str, Path(max_length=20)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    svc: Annotated[OAuthStartService, Depends(get_oauth_start_service_member)],
) -> MCPOAuthStartOut:
    """Workspace user starts OAuth for a workspace-private install they created.

    Permission rules (creator-only): the install must be workspace-private
    (``owner_workspace_id == workspace_id``) and the calling user must be
    the original creator. Org admins use the admin endpoint above.
    """
    server = await svc.server_repo.get(install_id)
    if server is None or server.org_id != ctx.org_id or server.owner_workspace_id != workspace_id:
        raise HTTPException(
            404,
            detail={
                "code": "mcp_oauth.install_not_found",
                "message": "MCP install not found.",
            },
        )
    if server.created_by_user_id != ctx.user.id:
        raise HTTPException(
            403,
            detail={
                "code": "mcp_oauth.permission_denied",
                "message": "Only the install creator may start OAuth for this install.",
            },
        )

    try:
        result = await svc.start(install_id=install_id, actor_user_id=ctx.user.id)
    except (
        MCPServerNotFound,
        MCPCatalogConnectorNotFound,
        OAuthInvalidServerState,
    ) as exc:
        raise _map_start_exception(exc) from exc

    _set_ticket_cookie(response, result.cookie_value)
    return MCPOAuthStartOut(authorize_url=result.authorize_url, state=result.state)


# ---------------------------------------------------------------------------
# 5.3 — GET /api/v1/oauth/mcp/callback
# ---------------------------------------------------------------------------


def _frontend_return_url() -> str:
    base = str(config.get("frontend_base_url", "http://localhost:3000")).rstrip("/")
    return f"{base}/oauth/mcp/return"


def _redirect(install_id: str, status: str, reason: str | None = None) -> RedirectResponse:
    params: dict[str, str] = {"install_id": install_id, "status": status}
    if reason:
        params["reason"] = reason
    url = f"{_frontend_return_url()}?{urlencode(params)}"
    resp = RedirectResponse(url=url, status_code=302)
    _strip_ticket_cookie(resp)
    return resp


@oauth_callback_router.get("/callback", include_in_schema=True)
async def oauth_callback(
    request: Request,
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    redis: Annotated[Redis, Depends(get_redis)],
    handler: Annotated[OAuthCallbackHandler, Depends(get_oauth_callback_handler)],
) -> RedirectResponse:
    """Authorization-code callback. Always 302s to the frontend return page.

    Errors surface as ``status=error&reason=<code>`` in the query string —
    no HTTP error pages, no exception bodies. The cookie is always
    stripped on the way out.
    """
    ticket = request.cookies.get(CALLBACK_TICKET_COOKIE_NAME)
    if not ticket:
        return _redirect("", "error", "callback_ticket_missing")

    ticket_key = CALLBACK_TICKET_REDIS_KEY_PREFIX + ticket
    expected_user_raw = await redis.get(ticket_key)
    if expected_user_raw is None:
        return _redirect("", "error", "callback_ticket_expired")
    await redis.delete(ticket_key)
    expected_user = (
        expected_user_raw.decode("utf-8")
        if isinstance(expected_user_raw, bytes)
        else str(expected_user_raw)
    )

    try:
        result = await handler.handle_callback(
            state=state,
            code=code,
            expected_actor_user_id=expected_user,
        )
    except (OAuthStateInvalid, OAuthStateExpired):
        logger.warning("OAuth callback rejected: state_invalid")
        return _redirect("", "error", "state_invalid")
    except OAuthPKCEMissing:
        logger.warning("OAuth callback rejected: pkce_missing")
        return _redirect("", "error", "pkce_missing")
    except OAuthCallbackError as exc:
        logger.warning("OAuth callback rejected: token_exchange_failed status={}", exc.status)
        return _redirect("", "error", "token_exchange_failed")
    except OAuthInvalidServerState:
        logger.warning("OAuth callback rejected: invalid_server_state")
        return _redirect("", "error", "invalid_server_state")
    except Exception:
        logger.exception("OAuth callback unexpected error")
        return _redirect("", "error", "internal_error")

    return _redirect(result.install_id, "ok")
