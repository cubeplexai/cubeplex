"""Outcome of one ``_sync_skills`` invocation.

Returned to the controller so it can decide whether to emit a sync event
and update the UserSandbox snapshot. See spec §4.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SyncResult:
    started_at: datetime
    finished_at: datetime
    status: str  # "noop" | "success" | "failed"
    n_pushed: int = 0
    n_removed: int = 0
    tar_size_bytes: int | None = None
    manifest: dict[str, Any] | None = None  # desired manifest = snapshot to mirror
    manifest_hash: str | None = None  # sha256 of canonical manifest dump
    skills_count: int = 0
    error_type: str | None = None
    error_message: str | None = None
