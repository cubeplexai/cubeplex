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
    """Return cubebox's provider-preset catalog as a nested vendor list (spec §5.1)."""
    from cubebox.llm.catalog import load_catalog

    return load_catalog().to_api()
