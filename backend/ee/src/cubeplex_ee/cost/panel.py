"""Mounts the cost reporting router through the host's extension hook."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter

    from cubeplex.plugins.protocols import AdminNavItem


class CostPanel:
    """Supplies a router and deliberately nothing else.

    No nav item: the host already carries an edition-gated ``/admin/insights``
    entry, and manifest nav items are rendered as links into ``/admin/ext/*``,
    an iframe surface for panels that ship their own UI. Returning one here
    would add a duplicate link that loads this JSON API inside an iframe.

    No static path: there is no bundled UI to serve — the reporting page is a
    native one in the host frontend.
    """

    def get_router(self) -> APIRouter | None:
        from cubeplex_ee.cost.routes import router

        return router

    def get_nav_items(self) -> list[AdminNavItem]:
        return []

    def get_static_path(self) -> Path | None:
        return None
