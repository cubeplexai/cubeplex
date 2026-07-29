"""E2E tests for the CE/EE plugin architecture (Task 24).

These tests exercise the real FastAPI application (with DB + lifespan) to verify
that the PluginRegistry resolves CE defaults correctly, that the admin extensions
manifest endpoint is auth-gated, that require_admin enforces role-based access, and
that the DefaultAuditSink records events via the stdlib logger.
"""

from __future__ import annotations

import logging

import httpx
import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_ce_defaults_load_after_lifespan(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """CE-only deployment: registry binds all singular Protocols to defaults;
    audit has at least the default sink; no syncers; admin_panel has at most default.
    """
    from cubeplex.plugins import get_registry
    from cubeplex.plugins.defaults.admin_panel import DefaultAdminPanelExtension
    from cubeplex.plugins.defaults.audit import DefaultAuditSink
    from cubeplex.plugins.defaults.auth import DefaultAuthProvider
    from cubeplex.plugins.defaults.permissions import DefaultPermissionChecker

    _client, _ws_id = admin_client  # ensure lifespan has run via the fixture

    reg = get_registry()
    assert isinstance(reg.get_auth_provider(), DefaultAuthProvider)
    assert isinstance(reg.get_permission_checker(), DefaultPermissionChecker)

    sinks = reg.get_audit_sinks()
    assert any(isinstance(s, DefaultAuditSink) for s in sinks)

    syncers = reg.get_user_directory_syncers()
    assert syncers == []

    exts = reg.get_admin_panel_extensions()
    assert all(isinstance(e, DefaultAdminPanelExtension) for e in exts)

    # RouteExtension has no CE default, so the reserved namespace stays empty on
    # an OSS deployment. The positive path — a registered extension actually
    # mounting under it — is proven by the licensed lane, which mounts a real
    # router there rather than a stand-in.
    assert reg.get_route_extensions() == []
    resp = await _client.get("/api/v1/_extensions/anything")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_extensions_manifest_ce_is_empty(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Authenticated user gets an empty manifest in CE-only deployment."""
    client, _ws_id = admin_client
    resp = await client.get("/api/v1/admin/_extensions/manifest")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_admin_extensions_manifest_requires_auth(
    unauthenticated_memory_client: httpx.AsyncClient,
) -> None:
    """Unauthenticated request to manifest returns 401."""
    resp = await unauthenticated_memory_client.get("/api/v1/admin/_extensions/manifest")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_only_route_denies_member(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """require_admin -> PermissionChecker.check -> denies non-admin."""
    client, ws_id = member_client
    resp = await client.patch(
        f"/api/v1/workspaces/{ws_id}",
        json={"name": "renamed-by-member"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_only_route_allows_admin(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """require_admin -> PermissionChecker.check -> allows admin."""
    client, ws_id = admin_client
    resp = await client.patch(
        f"/api/v1/workspaces/{ws_id}",
        json={"name": "renamed-by-admin"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_workspace_rename_emits_audit_event(
    admin_client: tuple[httpx.AsyncClient, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """After workspace rename, the DefaultAuditSink records workspace.renamed."""
    client, ws_id = admin_client

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="cubeplex.audit"):
        resp = await client.patch(
            f"/api/v1/workspaces/{ws_id}",
            json={"name": "audit-renamed"},
        )
        assert resp.status_code == 200

    messages = [r.getMessage() for r in caplog.records if r.name == "cubeplex.audit"]
    assert any("workspace.renamed" in m for m in messages), (
        f"no audit log for workspace.renamed; captured: {messages}"
    )


@pytest.mark.asyncio
async def test_route_extension_mounts_under_reserved_namespace() -> None:
    """A registered RouteExtension is reachable, and only under its own prefix.

    Registers a stand-in rather than waiting for a real licensed router, because
    the mount loop is the thing under test. The extension deliberately also
    declares a path core already owns: prefixing means that path lands under the
    extension's namespace instead of colliding, so neither router is ambiguous
    about what it serves.
    """
    import httpx as _httpx
    from fastapi import APIRouter, FastAPI

    from cubeplex.plugins import get_registry, reset_registry_for_tests
    from tests.e2e.conftest import _lifespan_context, _make_memory_test_app

    router = APIRouter()

    @router.get("/ping")
    async def _ping() -> dict[str, str]:
        return {"pong": "yes"}

    # A path core already serves. Prefixing relocates it rather than colliding.
    @router.get("/api/v1/system/info")
    async def _collides() -> dict[str, str]:
        return {"from_extension": "yes"}

    class _StandInExtension:
        def get_router(self) -> APIRouter:
            return router

    reset_registry_for_tests()
    try:
        get_registry().register_route_extension(_StandInExtension())
        app: FastAPI = _make_memory_test_app()
        app.state.deployment_mode = "multi_tenant"
        async with _lifespan_context(app):
            transport = _httpx.ASGITransport(app=app)
            async with _httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                # __module__ of a class defined in this test file is the test module,
                # so the prefix is derived from its top-level package: "tests".
                resp = await c.get("/api/v1/_extensions/tests/ping")
                assert resp.status_code == 200
                assert resp.json() == {"pong": "yes"}

                # Unprefixed, the extension is not reachable at all.
                assert (await c.get("/ping")).status_code == 404

                # The colliding declaration lands inside the namespace...
                nested = await c.get("/api/v1/_extensions/tests/api/v1/system/info")
                assert nested.json() == {"from_extension": "yes"}

                # ...and core's own path is untouched. Mount order alone would
                # also give core this one, since it registers first; the prefix
                # is what stops that from being a load-order coincidence.
                info = await c.get("/api/v1/system/info")
                assert info.status_code == 200
                assert "from_extension" not in info.json()
    finally:
        reset_registry_for_tests()
