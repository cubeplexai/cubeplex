"""CE/EE plugin Protocols + registry binding."""

from cubeplex.plugins.protocols import (
    AdminNavItem,
    AdminPanelExtension,
    AuditEvent,
    AuditSink,
    AuthProvider,
    PermissionChecker,
    PermissionResource,
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
    "AdminNavItem",
    "AdminPanelExtension",
    "AuditEvent",
    "AuditSink",
    "AuthProvider",
    "PermissionChecker",
    "PermissionResource",
    "PluginRegistry",
    "SyncResult",
    "SyncSchedule",
    "UserDirectorySyncer",
    "ensure_registry_bound",
    "get_registry",
    "reset_registry_for_tests",
]


def ensure_registry_bound() -> None:
    """Idempotent: call from app startup or test fixtures to seed CE defaults."""
    reg = get_registry()
    if not reg.is_bound():
        reg.bind_defaults()
