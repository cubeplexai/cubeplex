"""Plugin Protocols + supporting dataclasses.

The optional ``cubeplex_ee`` distribution implements these; the CE defaults in
``plugins/defaults/`` implement them when EE is absent.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

from fastapi import APIRouter, Request

if TYPE_CHECKING:
    from cubeplex.models import User


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


@runtime_checkable
class RouteExtension(Protocol):
    """A router mounted outside the admin surface.

    ``AdminPanelExtension`` covers everything reachable from the admin panel,
    which is authenticated and org-scoped. Some licensed features also need
    endpoints that are neither: SSO's login flow is called by the browser before
    any session exists, and by the identity provider itself.

    Mounted under ``/api/v1/_extensions/<pkg>/``, where ``<pkg>`` is the
    top-level module name — the same rule ``AdminPanelExtension`` uses one level
    down.

    The namespace is reserved rather than letting extensions mount at ``/api/v1``
    directly. Not because an extension could hijack a core path — routes are
    matched in registration order and core registers first, so today it would
    lose the collision — but because that protection is a load-order accident
    nobody enforces, and because losing silently is its own bug: an extension
    would serve a dead route with no error anywhere. Prefixing makes ownership
    unambiguous in both directions.
    """

    def get_router(self) -> APIRouter | None: ...
