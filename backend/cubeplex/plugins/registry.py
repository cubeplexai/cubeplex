"""CE/EE Protocol binding.

EE (the optional ``cubeplex_ee`` distribution) calls the ``register_*`` methods
during startup; ``bind_defaults`` then fills every slot EE left empty with the CE
default. There is no discovery step: absence of the EE distribution is what makes
a deployment OSS. See docs/dev/specs/2026-07-07-oss-ee-split-design.md §9.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Holds the resolved implementation of each Protocol."""

    def __init__(self) -> None:
        self._auth_provider: object | None = None
        self._permission_checker: object | None = None
        self._audit_sinks: list[object] = []
        self._user_directory_syncers: list[object] = []
        self._admin_panel_extensions: list[object] = []
        self._route_extensions: list[object] = []
        self._bound = False

    # ---------------- EE registration surface ---------------- #

    def register_auth_provider(self, provider: object) -> None:
        if self._auth_provider is not None:
            raise RuntimeError("auth_provider already registered")
        self._auth_provider = provider

    def register_permission_checker(self, checker: object) -> None:
        if self._permission_checker is not None:
            raise RuntimeError("permission_checker already registered")
        self._permission_checker = checker

    def register_audit_sink(self, sink: object) -> None:
        self._audit_sinks.append(sink)

    def register_user_directory_syncer(self, syncer: object) -> None:
        self._user_directory_syncers.append(syncer)

    def register_admin_panel_extension(self, ext: object) -> None:
        self._admin_panel_extensions.append(ext)

    def register_route_extension(self, ext: object) -> None:
        self._route_extensions.append(ext)

    # ---------------- CE fallback ---------------- #

    def bind_defaults(self) -> None:
        """Fill every slot EE left empty with its CE default. Idempotent."""
        from cubeplex.plugins.defaults.admin_panel import DefaultAdminPanelExtension
        from cubeplex.plugins.defaults.audit import DefaultAuditSink
        from cubeplex.plugins.defaults.auth import DefaultAuthProvider
        from cubeplex.plugins.defaults.permissions import DefaultPermissionChecker

        if self._bound:
            return
        # Construct every default before mutating anything: a constructor that
        # raises halfway must not leave appended sinks behind for a retry to
        # duplicate. Today's CE defaults have no __init__ and cannot raise, but
        # the EE registration that will run just before this can.
        auth = self._auth_provider if self._auth_provider is not None else DefaultAuthProvider()
        permissions = (
            self._permission_checker
            if self._permission_checker is not None
            else DefaultPermissionChecker()
        )
        audit_sink = DefaultAuditSink()
        admin_panel = DefaultAdminPanelExtension()

        self._auth_provider = auth
        self._permission_checker = permissions
        self._audit_sinks.append(audit_sink)
        self._admin_panel_extensions.append(admin_panel)
        # No CE default for route extensions: an OSS deployment genuinely has
        # none, and the mount loop over an empty list is the correct no-op.
        # AdminPanelExtension has one only because the nav manifest endpoint
        # needs something to ask.
        self._bound = True
        logger.info(
            "plugin registry bound: auth=%s permissions=%s audit_sinks=%d "
            "syncers=%d admin_extensions=%d route_extensions=%d",
            type(self._auth_provider).__name__,
            type(self._permission_checker).__name__,
            len(self._audit_sinks),
            len(self._user_directory_syncers),
            len(self._admin_panel_extensions),
            len(self._route_extensions),
        )

    def is_bound(self) -> bool:
        return self._bound

    # ---------------- accessors ---------------- #

    def get_auth_provider(self) -> object:
        if self._auth_provider is None:
            raise RuntimeError("call bind_defaults() first")
        return self._auth_provider

    def get_permission_checker(self) -> object:
        if self._permission_checker is None:
            raise RuntimeError("call bind_defaults() first")
        return self._permission_checker

    def get_audit_sinks(self) -> list[object]:
        return self._audit_sinks

    def get_user_directory_syncers(self) -> list[object]:
        return self._user_directory_syncers

    def get_admin_panel_extensions(self) -> list[object]:
        return self._admin_panel_extensions

    def get_route_extensions(self) -> list[object]:
        return self._route_extensions


# Module-level singleton, populated by app startup.
_registry: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry


def reset_registry_for_tests() -> None:
    global _registry
    _registry = None
