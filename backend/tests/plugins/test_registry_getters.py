import pytest

from cubeplex.plugins import (
    AdminPanelExtension,
    AuditSink,
    AuthProvider,
    PermissionChecker,
)
from cubeplex.plugins.registry import PluginRegistry


@pytest.mark.asyncio
async def test_get_auth_provider_falls_back_to_default() -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    assert isinstance(reg.get_auth_provider(), AuthProvider)


@pytest.mark.asyncio
async def test_get_permission_checker_falls_back_to_default() -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    assert isinstance(reg.get_permission_checker(), PermissionChecker)


@pytest.mark.asyncio
async def test_get_audit_sinks_returns_at_least_default() -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    sinks = reg.get_audit_sinks()
    assert len(sinks) >= 1
    assert all(isinstance(s, AuditSink) for s in sinks)


@pytest.mark.asyncio
async def test_get_admin_panel_extensions_empty_in_ce() -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    exts = reg.get_admin_panel_extensions()
    # CE-only: only the default returning empty everything
    assert all(isinstance(e, AdminPanelExtension) for e in exts)


@pytest.mark.asyncio
async def test_get_user_directory_syncers_empty_in_ce() -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    syncers = reg.get_user_directory_syncers()
    assert syncers == []  # No CE default for syncer
