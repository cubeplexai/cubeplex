"""Member-callable browser live-view endpoint under
/api/v1/ws/{workspace_id}/browser.

Returns an embeddable URL for the sandbox's Neko browser live view so the user
can watch and take over the sandbox browser from the frontend. See
docs/dev/specs/2026-05-20-sandbox-browser-takeover-design.md.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.sandbox.manager import get_sandbox_manager

router = APIRouter(prefix="/ws/{workspace_id}/browser", tags=["ws-browser"])


class BrowserLiveViewResponse(BaseModel):
    """A header-free URL the frontend can embed directly in an iframe."""

    url: str


@router.get("/live-view", response_model=BrowserLiveViewResponse)
async def get_live_view(
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> BrowserLiveViewResponse:
    """Resolve the caller's sandbox, ensure the browser stack is running, and
    return an embeddable live-view URL."""
    manager = get_sandbox_manager()
    sandbox = await manager.get_or_create(
        ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    await sandbox.start_browser()
    # Live-view / takeover traffic goes straight to Neko and bypasses the normal
    # per-tool activity updates, so mark the sandbox active here too (the
    # frontend also pings /keepalive while the view is open).
    await manager.touch(sandbox.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    endpoint = await sandbox.get_browser_endpoint()
    if endpoint.headers:
        # The endpoint requires request headers a browser cannot attach to an
        # iframe navigation. A same-origin reverse proxy is the planned path; do
        # not hand the frontend a URL it cannot authenticate.
        raise HTTPException(
            status_code=501,
            detail="sandbox browser endpoint requires header auth; same-origin proxy not yet implemented",
        )
    return BrowserLiveViewResponse(url=endpoint.url)


@router.post("/keepalive", status_code=status.HTTP_204_NO_CONTENT)
async def keepalive(
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    """Mark the sandbox active during a live-view/takeover session.

    Browser traffic goes directly to Neko, so without this a long human takeover
    (OAuth + 2FA, etc.) could be reaped by TTL cleanup. The frontend pings this
    on an interval while the live view is open."""
    manager = get_sandbox_manager()
    sandbox = await manager.get_or_create(
        ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    # force=True bypasses the touch_interval throttle so each keepalive reliably
    # extends the TTL even when the client cadence is below touch_interval.
    await manager.touch(sandbox.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id, force=True)
