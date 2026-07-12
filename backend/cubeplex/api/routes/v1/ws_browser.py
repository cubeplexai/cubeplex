"""Member-callable browser live-view endpoint under
/api/v1/ws/{workspace_id}/browser.

Returns an embeddable URL for the sandbox's Neko browser live view so the user
can watch and take over the sandbox browser from the frontend. See
docs/dev/specs/2026-05-20-sandbox-browser-takeover-design.md.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.routes.v1.ws_sandbox import _resolve_sandbox_scope
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.db.session import get_session
from cubeplex.sandbox import SandboxError
from cubeplex.sandbox.manager import get_sandbox_manager

router = APIRouter(prefix="/ws/{workspace_id}/browser", tags=["ws-browser"])

# Neko HTML5 client URL params, read client-side:
# - usr/pwd: auto-login (stripped after read). The password is the sandbox
#   image's fixed Neko user password; with session.implicit_hosting the member
#   auto-gets control, so the frontend "take over" works without an extra
#   in-Neko step. The endpoint is per-sandbox access-controlled, so this isn't a
#   meaningful secret.
# - embed/show_side/volume: strip Neko's own chrome (logo bar, side/chat panel)
#   and mute (muted autoplay avoids the "click to enable audio" overlay) so the
#   iframe shows just the browser desktop.
_NEKO_URL_PARAMS = {
    "usr": "cubeplex",
    "pwd": "neko",
    "embed": "1",
    "show_side": "0",
    "volume": "0",
}


class BrowserLiveViewResponse(BaseModel):
    """A header-free URL the frontend can embed directly in an iframe."""

    url: str


@router.get("/live-view", response_model=BrowserLiveViewResponse)
async def get_live_view(
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    conversation_id: str | None = Query(default=None),
) -> BrowserLiveViewResponse:
    """Resolve the caller's sandbox, ensure the browser stack is running, and
    return an embeddable live-view URL.

    ``conversation_id`` routes the lookup through ``_resolve_sandbox_scope`` so
    a participant of a standalone group chat or a topic conversation sees the
    shared sandbox's browser, not their own personal one.
    """
    manager = get_sandbox_manager()
    scope_type, scope_id, owner_user_id = await _resolve_sandbox_scope(
        session, ctx, conversation_id
    )
    try:
        attachment = await manager.get_or_create(
            scope_type=scope_type,
            scope_id=scope_id,
            user_id=owner_user_id,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
        )
        sandbox = attachment.sandbox
        await sandbox.start_browser()
        # Live-view / takeover traffic goes straight to Neko and bypasses the normal
        # per-tool activity updates, so mark the sandbox active here too (the
        # frontend also pings /keepalive while the view is open).
        await manager.touch(sandbox.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
        # Lease the sandbox for this bounded request so the idle-pause reaper
        # can't snipe it between start_browser and the endpoint resolve. The
        # lease expires naturally after ``lease_seconds`` — we deliberately
        # do NOT call ``release_lease`` because an overlapping caller (e.g.
        # the keepalive request) may have renewed the lease for a longer
        # window in the meantime, and a blind unconditional null would erase
        # that holder's protection (codex review P2 round 13). The keepalive
        # path renews while the panel stays open, so natural expiry is the
        # right safety net.
        await manager.renew_lease(sandbox.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
        endpoint = await sandbox.get_browser_endpoint()
    except SandboxError as exc:
        # Provisioning the sandbox (or its browser) failed — e.g. the provider
        # timed out waiting for a cold-starting pod. Surface a retryable 503 with a
        # clear reason instead of a bare 500.
        logger.warning("browser live-view unavailable for workspace {}: {}", ctx.workspace_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox is starting up or temporarily unavailable; please retry shortly",
        ) from exc
    if endpoint.headers:
        # The endpoint requires request headers a browser cannot attach to an
        # iframe navigation. A same-origin reverse proxy is the planned path; do
        # not hand the frontend a URL it cannot authenticate.
        raise HTTPException(
            status_code=501,
            detail="sandbox browser endpoint requires header auth; same-origin proxy not yet implemented",
        )
    sep = "&" if "?" in endpoint.url else "?"
    url = f"{endpoint.url}{sep}{urlencode(_NEKO_URL_PARAMS)}"
    return BrowserLiveViewResponse(url=url)


@router.post("/keepalive", status_code=status.HTTP_204_NO_CONTENT)
async def keepalive(
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    conversation_id: str | None = Query(default=None),
) -> None:
    """Mark the sandbox active during a live-view/takeover session.

    Browser traffic goes directly to Neko, so without this a long human takeover
    (OAuth + 2FA, etc.) could be reaped by TTL cleanup. The frontend pings this
    on an interval while the live view is open.

    Touches only the *existing* sandbox — never provisions one — so a dead/reaped
    sandbox isn't silently re-created (and kept alive) behind a stale iframe."""
    manager = get_sandbox_manager()
    scope_type, scope_id, _owner_user_id = await _resolve_sandbox_scope(
        session, ctx, conversation_id
    )
    touched = await manager.touch_active(
        scope_type=scope_type,
        scope_id=scope_id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    if not touched:
        # No active sandbox row (absent / terminated / sandbox_id cleared). The
        # iframe is stale — tell the frontend to stop pinging rather than
        # silently 204-ing a keepalive that extended nothing.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="sandbox not active; live view should be closed",
        )
