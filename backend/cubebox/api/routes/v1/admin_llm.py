"""Admin-only LLM catalog endpoints. Gated by require_org_admin."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from cubebox.auth.dependencies import require_org_admin
from cubebox.models import User

router = APIRouter(prefix="/admin/llm", tags=["admin-llm"])


@router.get("/presets")
async def list_provider_presets(
    *,
    user: Annotated[User, Depends(require_org_admin)],
) -> list[dict[str, Any]]:
    """Return cubepi's bundled provider-preset catalog (read-only passthrough)."""
    from cubepi.providers.catalog import list_provider_presets as _list_presets

    return [p.model_dump(mode="json") for p in _list_presets()]
