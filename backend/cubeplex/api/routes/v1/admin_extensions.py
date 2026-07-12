"""GET /api/v1/admin/_extensions/manifest — aggregated nav items + iframe URLs."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends

from cubeplex.auth.dependencies import current_active_user
from cubeplex.models import User
from cubeplex.plugins import get_registry
from cubeplex.plugins.protocols import AdminPanelExtension

router = APIRouter(prefix="/admin/_extensions", tags=["admin"])


@router.get("/manifest")
async def get_manifest(
    _user: User = Depends(current_active_user),
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ext_obj in get_registry().get_admin_panel_extensions():
        ext = cast(AdminPanelExtension, ext_obj)
        nav_items = ext.get_nav_items()
        if not nav_items:
            continue
        plugin_name = type(ext).__module__.split(".")[0]
        out.append(
            {
                "plugin": plugin_name,
                "nav_items": [
                    {
                        "id": item.id,
                        "label": item.label,
                        "icon": item.icon,
                        "section": item.section,
                        "order": item.order,
                        "url_path": item.url_path,
                    }
                    for item in nav_items
                ],
                "iframe_base_url": f"/api/v1/admin/_extensions/{plugin_name}/",
            }
        )
    return out
