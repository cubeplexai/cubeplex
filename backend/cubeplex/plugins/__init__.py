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
    """The one registry startup sequence. Idempotent.

    Registration and binding must happen together: ``register_*`` refuses to fill a
    slot twice, so EE has to register before ``bind_defaults`` seeds the CE
    fallbacks. Keeping both inside this single function means app startup and the
    test fixtures cannot disagree about the order.
    """
    from cubeplex.plugins.ee import load_ee

    reg = get_registry()
    if reg.is_bound():
        return
    load_ee(reg)
    reg.bind_defaults()
