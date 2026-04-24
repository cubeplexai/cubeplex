"""Plugin discovery + resolution."""

from __future__ import annotations

import importlib.metadata
import logging
from typing import TYPE_CHECKING

from cubebox.plugins.protocols import (
    CUBEBOX_PLUGIN_API_VERSION,
    AdminPanelExtension,
    AuditSink,
    AuthProvider,
    PermissionChecker,
    PluginManifest,
    UserDirectorySyncer,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Per-Protocol entry_points group names. External wheels publish here.
GROUP_MANIFEST = "cubebox.plugin_manifest"
GROUP_AUTH = "cubebox.auth_provider"
GROUP_PERMISSIONS = "cubebox.permission_checker"
GROUP_AUDIT = "cubebox.audit_sink"
GROUP_DIRECTORY = "cubebox.user_directory_syncer"
GROUP_ADMIN_PANEL = "cubebox.admin_panel_extension"

PROTOCOL_GROUPS: dict[str, type] = {
    GROUP_AUTH: AuthProvider,
    GROUP_PERMISSIONS: PermissionChecker,
    GROUP_AUDIT: AuditSink,
    GROUP_DIRECTORY: UserDirectorySyncer,
    GROUP_ADMIN_PANEL: AdminPanelExtension,
}

# Reserved entry_point name for CE built-in implementations. External plugins
# may not use this name.
RESERVED_NAME = "builtin"


class PluginRegistry:
    """Singleton-style holder of discovered plugin classes + CE defaults."""

    def __init__(self) -> None:
        self._manifests: dict[str, PluginManifest] = {}  # plugin_name → manifest
        self._candidates: dict[str, dict[str, type]] = {
            group: {} for group in PROTOCOL_GROUPS
        }  # group → {entry_point_name → impl class}

    async def discover(self) -> None:
        """Scan all entry_points + validate manifests + collect candidates."""
        manifest_eps = list(importlib.metadata.entry_points(group=GROUP_MANIFEST))
        # Each plugin_manifest entry_point loads to a PluginManifest instance.
        plugin_dist_to_manifest: dict[str, PluginManifest] = {}
        for ep in manifest_eps:
            manifest = ep.load()
            if not isinstance(manifest, PluginManifest):
                raise RuntimeError(f"entry_point {ep.value} did not return a PluginManifest")
            if manifest.api_version != CUBEBOX_PLUGIN_API_VERSION:
                raise RuntimeError(
                    f"plugin {manifest.name!r}: api_version={manifest.api_version} "
                    f"but cubebox CE requires api_version={CUBEBOX_PLUGIN_API_VERSION}"
                )
            self._manifests[manifest.name] = manifest
            # Map dist name to manifest for cross-group lookup
            dist_name = self._dist_name(ep)
            if dist_name:
                plugin_dist_to_manifest[dist_name] = manifest
            logger.info(
                "registered plugin manifest: %s v%s (api=%d)",
                manifest.name,
                manifest.version,
                manifest.api_version,
            )

        # Walk per-Protocol groups; reject unknown plugins (no manifest)
        for group, _ in PROTOCOL_GROUPS.items():
            for ep in importlib.metadata.entry_points(group=group):
                if ep.name == RESERVED_NAME:
                    raise RuntimeError(
                        f"entry_point name {RESERVED_NAME!r} is reserved for CE; "
                        f"plugin {ep.value} cannot use it"
                    )
                dist_name = self._dist_name(ep)
                if dist_name and dist_name not in plugin_dist_to_manifest:
                    raise RuntimeError(
                        f"plugin {ep.value} (dist={dist_name}) is missing a "
                        f"{GROUP_MANIFEST} entry_point"
                    )
                self._candidates[group][ep.name] = ep.load()
                logger.info("registered candidate %s.%s = %s", group, ep.name, ep.value)

    @staticmethod
    def _dist_name(ep) -> str | None:  # type: ignore[no-untyped-def]
        try:
            return ep.dist.name if ep.dist else None
        except AttributeError:
            return None

    def resolve_singular(
        self,
        group: str,
        *,
        default: object,
        selected: str | None,
    ) -> object:
        """Resolve a singular Protocol candidate; instantiate or pass through default.

        Resolution rules:
        - selected="builtin" → CE default (forces fallback even if externals present)
        - selected="<name>"  → look up that entry_point name; raise if missing
        - selected=None      → 0 ext: default; 1 ext: that one; ≥2 ext: RuntimeError
        """
        candidates = self._candidates[group]

        if selected == RESERVED_NAME:
            return default
        if selected is not None:
            if selected not in candidates:
                raise RuntimeError(
                    f"{group}: 'selected' is {selected!r} but no such entry_point is "
                    f"not registered (available: {sorted(candidates)})"
                )
            return candidates[selected]()

        # selected is None — implicit rules
        if len(candidates) == 0:
            return default
        if len(candidates) == 1:
            (cls,) = candidates.values()
            return cls()
        raise RuntimeError(
            f"{group}: multiple entry_points registered ({sorted(candidates)}); "
            f"set plugins.{group.split('.')[1]}.selected = '<name>' to pick one"
        )

    def resolve_plural(
        self,
        group: str,
        *,
        default: object | None,
        disabled: list[str],
    ) -> list[object]:
        """Resolve all candidates for a plural Protocol; honor `disabled` filter.

        - default: optional CE built-in instance (registered as RESERVED_NAME)
        - disabled: list of entry_point names to exclude (incl. RESERVED_NAME for
          default)
        """
        disabled_set = set(disabled)
        out: list[object] = []
        if default is not None and RESERVED_NAME not in disabled_set:
            out.append(default)
        for name, cls in self._candidates[group].items():
            if name in disabled_set:
                continue
            out.append(cls())
        return out
