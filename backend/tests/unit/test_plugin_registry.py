"""Unit: registry binds CE defaults, and honours EE registrations when present."""

import pytest

from cubeplex.plugins.defaults.admin_panel import DefaultAdminPanelExtension
from cubeplex.plugins.defaults.audit import DefaultAuditSink
from cubeplex.plugins.defaults.auth import DefaultAuthProvider
from cubeplex.plugins.defaults.permissions import DefaultPermissionChecker
from cubeplex.plugins.registry import PluginRegistry


class _OtherAuthProvider:
    async def authenticate(self, request: object) -> None:
        return None

    def get_auth_routers(self) -> list[object]:
        return []


class _OtherRouteExtension:
    def get_router(self) -> None:
        return None


class _OtherAuditSink:
    async def record(self, event: object) -> None:
        return None


def test_ce_only_binds_every_default() -> None:
    reg = PluginRegistry()
    reg.bind_defaults()
    assert isinstance(reg.get_auth_provider(), DefaultAuthProvider)
    assert isinstance(reg.get_permission_checker(), DefaultPermissionChecker)
    assert any(isinstance(s, DefaultAuditSink) for s in reg.get_audit_sinks())
    assert reg.get_user_directory_syncers() == []
    assert all(isinstance(e, DefaultAdminPanelExtension) for e in reg.get_admin_panel_extensions())
    # No CE default here on purpose: an OSS deployment mounts no extra routers,
    # which is what makes /api/v1/_extensions/ empty rather than merely unused.
    assert reg.get_route_extensions() == []


def test_registered_auth_provider_wins_over_default() -> None:
    reg = PluginRegistry()
    provider = _OtherAuthProvider()
    reg.register_auth_provider(provider)
    reg.bind_defaults()
    assert reg.get_auth_provider() is provider


def test_registered_audit_sink_is_added_alongside_default() -> None:
    reg = PluginRegistry()
    sink = _OtherAuditSink()
    reg.register_audit_sink(sink)
    reg.bind_defaults()
    sinks = reg.get_audit_sinks()
    assert sink in sinks
    assert any(isinstance(s, DefaultAuditSink) for s in sinks)


def test_registered_route_extension_is_the_only_one() -> None:
    reg = PluginRegistry()
    ext = _OtherRouteExtension()
    reg.register_route_extension(ext)
    reg.bind_defaults()
    assert reg.get_route_extensions() == [ext]


def test_double_registration_of_singular_slot_raises() -> None:
    reg = PluginRegistry()
    reg.register_auth_provider(_OtherAuthProvider())
    with pytest.raises(RuntimeError, match="already registered"):
        reg.register_auth_provider(_OtherAuthProvider())


def test_getters_before_bind_defaults_raise() -> None:
    reg = PluginRegistry()
    with pytest.raises(RuntimeError, match="bind_defaults"):
        reg.get_auth_provider()
    with pytest.raises(RuntimeError, match="bind_defaults"):
        reg.get_permission_checker()


def test_bind_defaults_is_idempotent() -> None:
    reg = PluginRegistry()
    reg.bind_defaults()
    first = reg.get_auth_provider()
    sink_count = len(reg.get_audit_sinks())
    reg.bind_defaults()
    assert reg.get_auth_provider() is first
    assert len(reg.get_audit_sinks()) == sink_count


def test_registry_instances_do_not_share_state() -> None:
    """Plural slots were class attributes before the simplification."""
    first = PluginRegistry()
    first.register_audit_sink(_OtherAuditSink())
    second = PluginRegistry()
    assert second.get_audit_sinks() == []


def test_failed_bind_leaves_nothing_appended(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising default must not leave state a retry would duplicate."""

    def _boom() -> object:
        raise RuntimeError("constructor exploded")

    monkeypatch.setattr("cubeplex.plugins.defaults.admin_panel.DefaultAdminPanelExtension", _boom)
    reg = PluginRegistry()
    with pytest.raises(RuntimeError, match="exploded"):
        reg.bind_defaults()
    assert reg.is_bound() is False
    assert reg.get_audit_sinks() == []
    assert reg.get_admin_panel_extensions() == []

    monkeypatch.undo()
    reg.bind_defaults()
    assert len(reg.get_audit_sinks()) == 1
