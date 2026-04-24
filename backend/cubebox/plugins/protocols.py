"""Plugin Protocols + supporting dataclasses + version constant.

CUBEBOX_PLUGIN_API_VERSION is the single integer plugins must declare via
their PluginManifest. Mismatch → registry refuses to load the plugin.
"""

from dataclasses import dataclass, field  # noqa: F401
from datetime import datetime
from pathlib import Path  # noqa: F401
from typing import Any, Final, Protocol, runtime_checkable  # noqa: F401
from uuid import UUID

from fastapi import APIRouter, Request  # noqa: F401

CUBEBOX_PLUGIN_API_VERSION: Final[int] = 1


@dataclass(frozen=True)
class PluginManifest:
    """Plugin self-describing metadata. Required entry_point per wheel."""

    api_version: int
    name: str
    version: str
    description: str = ""


@dataclass(frozen=True)
class PermissionResource:
    """Identifies the target of a permission check."""

    type: str  # "workspace" | "organization" | "conversation" | ...
    id: UUID | None  # None = type-level policy
    org_id: UUID | None = None
    workspace_id: UUID | None = None


@dataclass(frozen=True)
class AuditEvent:
    timestamp: datetime
    user_id: UUID | None
    org_id: UUID | None
    workspace_id: UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    user_agent: str | None
    metadata: dict[str, Any]


@dataclass
class SyncResult:
    added: int
    updated: int
    removed: int
    errors: list[str]


@dataclass
class SyncSchedule:
    interval_seconds: int | None  # None = on-demand only


@dataclass(frozen=True)
class AdminNavItem:
    id: str
    label: str
    icon: str | None
    section: str  # "identity" | "integrations" | "settings" | "custom"
    order: int
    url_path: str
