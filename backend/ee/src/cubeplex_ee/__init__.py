"""Commercially licensed CubePlex features.

Governed by ``backend/ee/LICENSE``, not the repository's Apache-2.0 licence.

The host imports this package once during startup and calls :func:`register`,
handing over the verified licence. Nothing here is imported by the core package,
which is what lets the core run without this distribution installed at all.

The relocations that fill it in are staged in
``docs/dev/specs/2026-07-07-oss-ee-split-design.md`` §11.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from cubeplex.plugins.license import License

logger = logging.getLogger(__name__)

__version__ = "0.3.0"


class _Registry(Protocol):
    """The slice of the host registry this package is allowed to touch.

    Structural, so nothing here imports the host at module scope. The host owns
    the real definition in ``cubeplex/plugins/registry.py``; if these signatures
    drift apart, mypy fails here rather than at runtime on a customer's box.
    """

    def register_auth_provider(self, provider: object) -> None: ...

    def register_permission_checker(self, checker: object) -> None: ...

    def register_audit_sink(self, sink: object) -> None: ...

    def register_user_directory_syncer(self, syncer: object) -> None: ...

    def register_admin_panel_extension(self, ext: object) -> None: ...

    def register_route_extension(self, ext: object) -> None: ...


def register(registry: _Registry, *, license: License | Any) -> None:
    """Bind this package's implementations onto the host registry.

    Called exactly once, before the host fills the remaining slots with its own
    defaults, so anything left unregistered here falls back cleanly. The licence
    is passed in rather than re-read from configuration: the host has already
    verified it, and per-feature checks belong to whichever feature needs them.
    """
    from cubeplex_ee.cost import CostPanel
    from cubeplex_ee.sso import SSOAdminPanel, SSOLoginRoutes

    features = sorted(getattr(license, "features", ()) or ())
    logger.info(
        "licensed package %s active for %s (features=%s)",
        __version__,
        getattr(license, "licensee", "unknown"),
        features,
    )
    registry.register_admin_panel_extension(CostPanel())
    registry.register_admin_panel_extension(SSOAdminPanel())
    registry.register_route_extension(SSOLoginRoutes())


__all__ = ["__version__", "register"]
