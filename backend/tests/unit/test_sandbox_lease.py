"""Unit test: ws_browser.get_live_view renews the in-use lease, and does
NOT explicitly release it (codex review P2 round 13).

The live-view direct path bypasses LazySandbox, so the route must call
``manager.renew_lease`` alongside the existing ``touch``. It must NOT call
``release_lease`` because an overlapping caller (e.g. the keepalive request)
may have renewed the lease for a longer window in the meantime, and an
unconditional null would erase that holder's protection. The lease expires
naturally after ``lease_seconds``; keepalive renews while the panel is open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from cubebox.api.routes.v1 import ws_browser as ws_browser_routes
from cubebox.sandbox.base import BrowserEndpoint


@dataclass
class _FakeUser:
    id: str


@dataclass
class _FakeCtx:
    user: _FakeUser
    org_id: str
    workspace_id: str
    role: Any = None


class _FakeSandbox:
    """Stub sandbox: records start_browser + returns a header-free endpoint."""

    def __init__(self, sandbox_id: str) -> None:
        self.id = sandbox_id
        self.started = False

    async def start_browser(self) -> None:
        self.started = True

    async def get_browser_endpoint(self) -> BrowserEndpoint:
        return BrowserEndpoint(url="http://neko.example/")


class _FakeManager:
    """Records manager calls so the test can assert lease semantics."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._sandbox = _FakeSandbox("sbx-test-1")

    async def get_or_create(
        self,
        *,
        scope_type: str,
        scope_id: str,
        user_id: str,
        org_id: str,
        workspace_id: str,
    ) -> _FakeSandbox:
        self.calls.append(
            (
                "get_or_create",
                {
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "user_id": user_id,
                    "org_id": org_id,
                    "workspace_id": workspace_id,
                },
            )
        )
        return self._sandbox

    async def touch(self, sandbox_id: str, *, org_id: str, workspace_id: str) -> None:
        self.calls.append(
            ("touch", {"sandbox_id": sandbox_id, "org_id": org_id, "workspace_id": workspace_id})
        )

    async def renew_lease(
        self,
        sandbox_id: str,
        *,
        org_id: str,
        workspace_id: str,
        lease_seconds: int | None = None,
    ) -> None:
        self.calls.append(
            (
                "renew_lease",
                {
                    "sandbox_id": sandbox_id,
                    "org_id": org_id,
                    "workspace_id": workspace_id,
                    "lease_seconds": lease_seconds,
                },
            )
        )

    async def release_lease(self, sandbox_id: str, *, org_id: str, workspace_id: str) -> None:
        # Recorded so the test can assert it is NOT called.
        self.calls.append(
            (
                "release_lease",
                {"sandbox_id": sandbox_id, "org_id": org_id, "workspace_id": workspace_id},
            )
        )


@pytest.mark.asyncio
async def test_get_live_view_renews_lease_without_releasing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_live_view`` must renew the lease so the idle-pause reaper can't
    snipe the sandbox mid-request, but it must NOT release the lease at the
    end — an overlapping keepalive may have extended it, and an unconditional
    release would erase that protection (codex P2 round 13).
    """
    fake_mgr = _FakeManager()
    monkeypatch.setattr(ws_browser_routes, "get_sandbox_manager", lambda: fake_mgr)

    ctx = _FakeCtx(user=_FakeUser(id="user-1"), org_id="org-1", workspace_id="ws-1")

    # session arg is unused on the no-conversation path; the resolver
    # short-circuits to (user, ctx.user.id, ctx.user.id).
    resp = await ws_browser_routes.get_live_view(ctx, session=None, conversation_id=None)  # type: ignore[arg-type]

    assert resp.url.startswith("http://neko.example/")

    names = [c[0] for c in fake_mgr.calls]
    assert "renew_lease" in names, f"expected renew_lease in calls, got {names}"
    # Crucially, NO release_lease — natural expiry is the model now.
    assert "release_lease" not in names, (
        f"get_live_view must not call release_lease (codex P2 round 13); got {names}"
    )

    renew = next(c for c in fake_mgr.calls if c[0] == "renew_lease")
    assert renew[1]["sandbox_id"] == fake_mgr._sandbox.id
    assert renew[1]["org_id"] == "org-1"
    assert renew[1]["workspace_id"] == "ws-1"
