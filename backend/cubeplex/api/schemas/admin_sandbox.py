"""Admin API response models for /api/v1/admin/sandboxes/*."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class UserSandboxSnapshotOut(BaseModel):
    id: str
    org_id: str
    workspace_id: str
    user_id: str
    scope_type: str
    scope_id: str
    sandbox_id: str | None
    status: str
    image: str
    last_activity_at: datetime | None
    # Skill sync snapshot
    skills_manifest_hash: str | None
    skills_count: int
    last_skill_sync_at: datetime | None
    last_skill_sync_event_id: str | None


class SyncEventOut(BaseModel):
    id: str
    org_id: str
    workspace_id: str
    user_sandbox_id: str
    started_at: datetime
    finished_at: datetime
    status: str
    n_pushed: int
    n_removed: int
    tar_size_bytes: int | None
    error_type: str | None
    error_message: str | None
    manifest_snapshot: dict[str, Any] | None


class PaginationParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
