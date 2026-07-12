"""CE default AdminPanelExtension: empty (CE itself contributes nothing)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from cubeplex.plugins.protocols import AdminNavItem


class DefaultAdminPanelExtension:
    def get_router(self) -> APIRouter | None:
        return None

    def get_nav_items(self) -> list[AdminNavItem]:
        return []

    def get_static_path(self) -> Path | None:
        return None
