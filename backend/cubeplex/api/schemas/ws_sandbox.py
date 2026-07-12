"""Response models for ws user sandbox routes (/api/v1/ws/{ws}/sandboxes)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MySandboxOut(BaseModel):
    """A sandbox entity owned by the calling user, as seen on the user-facing
    sandbox settings panel.

    Hides provider-internal fields (``sandbox_id``, ``skills_manifest_hash``,
    etc.) — those live on the admin observability surface.
    """

    id: str
    scope_type: str
    scope_id: str
    scope_title: str | None
    status: str
    image: str
    last_activity_at: datetime | None
    created_at: datetime
