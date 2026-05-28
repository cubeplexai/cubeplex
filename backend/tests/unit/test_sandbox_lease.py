"""Unit test: ws_browser.get_live_view renews + releases the in-use lease.

The live-view direct path bypasses LazySandbox, so the route must call
``manager.renew_lease`` (alongside the existing ``touch``) and
``manager.release_lease`` in a finally — not just ``touch``.
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

    async def get_or_create(self, user_id: str, *, org_id: str, workspace_id: str) -> _FakeSandbox:
        self.calls.append(
            ("get_or_create", {"user_id": user_id, "org_id": org_id, "workspace_id": workspace_id})
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
        self.calls.append(
            (
                "release_lease",
                {"sandbox_id": sandbox_id, "org_id": org_id, "workspace_id": workspace_id},
            )
        )


@pytest.mark.asyncio
async def test_get_live_view_renews_and_releases_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mgr = _FakeManager()
    monkeypatch.setattr(ws_browser_routes, "get_sandbox_manager", lambda: fake_mgr)

    ctx = _FakeCtx(user=_FakeUser(id="user-1"), org_id="org-1", workspace_id="ws-1")

    resp = await ws_browser_routes.get_live_view(ctx)  # type: ignore[arg-type]

    assert resp.url.startswith("http://neko.example/")

    names = [c[0] for c in fake_mgr.calls]
    assert "renew_lease" in names, f"expected renew_lease in calls, got {names}"
    assert "release_lease" in names, f"expected release_lease in calls, got {names}"

    # renew_lease must target the same sandbox_id as touch.
    renew = next(c for c in fake_mgr.calls if c[0] == "renew_lease")
    assert renew[1]["sandbox_id"] == fake_mgr._sandbox.id
    assert renew[1]["org_id"] == "org-1"
    assert renew[1]["workspace_id"] == "ws-1"

    release = next(c for c in fake_mgr.calls if c[0] == "release_lease")
    assert release[1]["sandbox_id"] == fake_mgr._sandbox.id

    # release must follow renew (finally runs last).
    assert names.index("release_lease") > names.index("renew_lease")
