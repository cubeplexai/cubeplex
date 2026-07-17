"""Plugin Protocols + supporting dataclasses + version constant.

CUBEPLEX_PLUGIN_API_VERSION is the single integer plugins must declare via
their PluginManifest. Mismatch → registry refuses to load the plugin.
"""

from dataclasses import dataclass, field  # noqa: F401
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable
from uuid import UUID

from fastapi import APIRouter, Request

if TYPE_CHECKING:
    from cubeplex.models import User

CUBEPLEX_PLUGIN_API_VERSION: Final[int] = 1


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


@runtime_checkable
class AuthProvider(Protocol):
    """Authenticate requests and yield a User principal."""

    async def authenticate(self, request: Request) -> "User | None": ...

    def get_auth_routers(self) -> list[APIRouter]: ...


@runtime_checkable
class PermissionChecker(Protocol):
    async def check(
        self,
        user: "User",
        action: str,
        resource: PermissionResource,
    ) -> bool: ...


@runtime_checkable
class AuditSink(Protocol):
    async def record(self, event: AuditEvent) -> None: ...


@runtime_checkable
class UserDirectorySyncer(Protocol):
    async def sync(self) -> SyncResult: ...

    def get_schedule(self) -> SyncSchedule: ...


@runtime_checkable
class AdminPanelExtension(Protocol):
    def get_router(self) -> APIRouter | None: ...

    def get_nav_items(self) -> list[AdminNavItem]: ...

    def get_static_path(self) -> Path | None: ...
