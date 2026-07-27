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
    slot twice, so anything that registers (EE, once it exists) has to run before
    ``bind_defaults`` seeds the CE fallbacks. Keeping both inside this single
    function means app startup and the test fixtures cannot disagree about the
    order — the future ``load_ee(reg)`` call goes here, above ``bind_defaults``,
    and every caller inherits it.
    """
    reg = get_registry()
    if reg.is_bound():
        return
    reg.bind_defaults()
