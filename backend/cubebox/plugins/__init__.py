"""CE/EE plugin protocols + entry_points-based discovery."""

from cubebox.plugins.protocols import (
    CUBEBOX_PLUGIN_API_VERSION,
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
from cubebox.plugins.registry import (
    PluginRegistry,
    get_registry,
    reset_registry_for_tests,
)

__all__ = [
    "CUBEBOX_PLUGIN_API_VERSION",
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
    "get_registry",
    "reset_registry_for_tests",
]
