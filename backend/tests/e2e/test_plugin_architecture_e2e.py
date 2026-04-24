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


@pytest.mark.asyncio
async def test_ce_defaults_load_after_lifespan(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """CE-only deployment: registry binds all singular Protocols to defaults;
    audit has at least the default sink; no syncers; admin_panel has at most default.
    """
    from cubebox.plugins import get_registry
    from cubebox.plugins.defaults.admin_panel import DefaultAdminPanelExtension
    from cubebox.plugins.defaults.audit import DefaultAuditSink
    from cubebox.plugins.defaults.auth import DefaultAuthProvider
    from cubebox.plugins.defaults.permissions import DefaultPermissionChecker

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
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/invites",
        json={"role": "member"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_only_route_allows_admin(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """require_admin -> PermissionChecker.check -> allows admin."""
    client, ws_id = admin_client
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/invites",
        json={"role": "member"},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_invite_creation_emits_audit_event(
    admin_client: tuple[httpx.AsyncClient, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """After invite creation, the DefaultAuditSink records workspace.invite_created."""
    client, ws_id = admin_client

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="cubebox.audit"):
        resp = await client.post(
            f"/api/v1/workspaces/{ws_id}/invites",
            json={"role": "member"},
        )
        assert resp.status_code == 201

    messages = [r.getMessage() for r in caplog.records if r.name == "cubebox.audit"]
    assert any("workspace.invite_created" in m for m in messages), (
        f"no audit log for invite_created; captured: {messages}"
    )
