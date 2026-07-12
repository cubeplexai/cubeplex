"""CE/EE plugin protocols + entry_points-based discovery."""

from cubeplex.plugins.protocols import (
    CUBEPLEX_PLUGIN_API_VERSION,
    AdminNavItem,
    AdminPanelExtension,
    AuditEvent,
    AuditSink,
    AuthProvider,
    PermissionChecker,
    PermissionResource,
    PluginManifest,
    SyncResult,
    SyncSchedule,
    UserDirectorySyncer,
)
from cubeplex.plugins.registry import (
    PluginRegistry,
    get_registry,
    reset_registry_for_tests,
)

__all__ = [
    "CUBEPLEX_PLUGIN_API_VERSION",
    "AdminNavItem",
    "AdminPanelExtension",
    "AuditEvent",
    "AuditSink",
    "AuthProvider",
    "PermissionChecker",
    "PermissionResource",
    "PluginManifest",
    "PluginRegistry",
    "SyncResult",
    "SyncSchedule",
    "UserDirectorySyncer",
    "ensure_registry_bound",
    "get_registry",
    "reset_registry_for_tests",
]


def ensure_registry_bound() -> None:
    """Idempotent: call from app startup or test fixtures to seed defaults."""
    reg = get_registry()
    if reg._auth_provider is None:  # not yet bound
        reg.bind_defaults()
