"""The two mount points enterprise SSO needs.

Two, not one, because SSO spans both extension surfaces: administrators
configure a connection through the admin panel, but the login flow itself is
called before any session exists — by the browser, and by the identity provider
posting a SAML assertion. Those cannot live under ``/admin/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter

    from cubeplex.plugins.protocols import AdminNavItem


class SSOAdminPanel:
    """AdminPanelExtension: the connection CRUD surface.

    Mounted at ``/api/v1/admin/_extensions/cubeplex_ee/sso/*``.
    """

    def get_router(self) -> APIRouter | None:
        from cubeplex_ee.sso.admin_routes import router

        return router

    def get_nav_items(self) -> list[AdminNavItem]:
        # Empty for the same reason CostPanel's is: the web package still ships
        # the /admin/authentication page and its own nav entry, gated by edition.
        # Serving a second entry from here would render it twice.
        return []

    def get_static_path(self) -> Path | None:
        return None


class SSOLoginRoutes:
    """RouteExtension: the pre-login SAML/OIDC flow.

    Mounted at ``/api/v1/_extensions/cubeplex_ee/sso/*``. These paths are what an
    administrator registers with their identity provider, so they are spelled
    once in ``routes.PUBLIC_BASE_PATH``.
    """

    def get_router(self) -> APIRouter | None:
        from cubeplex_ee.sso.routes import router

        return router
