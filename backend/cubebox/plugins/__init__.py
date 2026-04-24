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
    "SyncResult",
    "SyncSchedule",
    "UserDirectorySyncer",
]
