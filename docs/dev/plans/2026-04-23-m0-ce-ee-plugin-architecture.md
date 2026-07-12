# M0 · CE/EE 插件架构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze 5 `Protocol` extension interfaces (AuthProvider / PermissionChecker / AuditSink / UserDirectorySyncer / AdminPanelExtension), wire `pip entry_points` discovery + CE default fallback, integrate AuthProvider + PermissionChecker into existing CE flows, hook 3 audit call sites, and stand up the AdminPanelExtension startup scan — all without breaking any existing auth / RBAC test.

**Architecture:** New package `backend/cubeplex/plugins/` holds Protocols + registry + CE defaults. CE default implementations are instantiated directly by the registry (no entry_points). External plugins declare entry_points under per-Protocol groups (`cubeplex.auth_provider` etc.) plus a mandatory `cubeplex.plugin_manifest` for API version checking. `Sandbox.execute / upload / download` are unchanged; only auth + admin plumbing touched.

**Tech Stack:** Python 3.12, FastAPI, fastapi-users, pytest, pytest-asyncio, importlib.metadata (entry_points), structlog, Pydantic, SQLModel, dynaconf.

**Spec:** `docs/superpowers/specs/2026-04-22-ce-ee-plugin-architecture-design.md`

---

## File Structure

### Create

```
backend/cubeplex/plugins/
├─ __init__.py                      # Re-export PluginManifest, registry getters, CUBEPLEX_PLUGIN_API_VERSION
├─ protocols.py                     # 5 Protocols + dataclasses + PluginManifest
├─ registry.py                      # PluginRegistry (discover, resolve, getters)
├─ audit.py                         # audit_log() helper
└─ defaults/
   ├─ __init__.py
   ├─ auth.py                       # DefaultAuthProvider wraps fastapi-users
   ├─ permissions.py                # DefaultPermissionChecker wraps Role lookup
   ├─ audit.py                      # DefaultAuditSink: structlog INFO no-op
   └─ admin_panel.py                # DefaultAdminPanelExtension: empty

backend/tests/plugins/
├─ __init__.py
├─ conftest.py                      # registry fixtures + helpers
└─ test_contracts.py                # 11 contract assertions

backend/tests/fixtures/fake_plugin/
├─ pyproject.toml                   # tomled in tmp install for tests
├─ fake_plugin/
│  ├─ __init__.py                   # exports MANIFEST + 5 plugin classes
│  ├─ auth.py                       # FakeAuthProvider
│  ├─ permissions.py                # FakePermissionChecker
│  ├─ audit.py                      # FakeAuditSink
│  ├─ directory.py                  # FakeUserDirectorySyncer
│  └─ admin_panel.py                # FakeAdminPanelExtension
```

### Modify

```
backend/cubeplex/auth/dependencies.py        # require_role rewritten to call PermissionChecker
backend/cubeplex/auth/users.py               # on_after_login hook + audit_log calls
backend/cubeplex/api/app.py                  # Plugin discovery startup; mount auth routers + admin extension routers + manifest endpoint
backend/cubeplex/api/routes/v1/workspaces.py # invite create → audit_log
backend/cubeplex/config.py                   # plugins.* pydantic schema
backend/config.yaml                         # plugins: section default
backend/config.development.yaml             # plugins: section default
backend/config.test.yaml                    # plugins: section default
.github/workflows/ci.yml                    # test-ee-compat placeholder job
```

---

## Tasks

### Task 1: Create `cubeplex.plugins` package skeleton

**Files:**
- Create: `backend/cubeplex/plugins/__init__.py`
- Create: `backend/cubeplex/plugins/defaults/__init__.py`
- Create: `backend/tests/plugins/__init__.py`

- [ ] **Step 1: Create directory + empty __init__.py files**

```bash
mkdir -p backend/cubeplex/plugins/defaults
mkdir -p backend/tests/plugins
touch backend/cubeplex/plugins/defaults/__init__.py
touch backend/tests/plugins/__init__.py
```

- [ ] **Step 2: Add placeholder `cubeplex/plugins/__init__.py` with module docstring**

```python
"""CE/EE plugin protocols + entry_points-based discovery.

This package defines the contracts that external plugins (e.g. cubeplex-ee)
implement to extend cubeplex CE. See docs/superpowers/specs/2026-04-22-ce-ee-plugin-architecture-design.md.
"""
```

- [ ] **Step 3: Verify import works**

Run: `cd backend && uv run python -c "import cubeplex.plugins"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/plugins/ backend/tests/plugins/
git commit -m "chore(plugins): create package skeleton for M0 plugin architecture"
```

---

### Task 2: Define `PluginManifest` + `CUBEPLEX_PLUGIN_API_VERSION` constant

**Files:**
- Create: `backend/cubeplex/plugins/protocols.py`
- Create: `backend/tests/plugins/test_protocols.py`

- [ ] **Step 1: Write failing test for PluginManifest dataclass**

```python
# backend/tests/plugins/test_protocols.py
from cubeplex.plugins.protocols import CUBEPLEX_PLUGIN_API_VERSION, PluginManifest


def test_plugin_manifest_constructs_with_required_fields() -> None:
    m = PluginManifest(api_version=1, name="test-plugin", version="0.1.0")
    assert m.api_version == 1
    assert m.name == "test-plugin"
    assert m.version == "0.1.0"
    assert m.description == ""


def test_plugin_manifest_accepts_description() -> None:
    m = PluginManifest(api_version=1, name="x", version="0.1.0", description="Hello")
    assert m.description == "Hello"


def test_api_version_constant_is_int_one() -> None:
    assert CUBEPLEX_PLUGIN_API_VERSION == 1
    assert isinstance(CUBEPLEX_PLUGIN_API_VERSION, int)
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd backend && uv run pytest tests/plugins/test_protocols.py -v`
Expected: FAIL with `ImportError: cannot import name 'PluginManifest' from 'cubeplex.plugins.protocols'`

- [ ] **Step 3: Implement protocols.py with manifest + version**

```python
# backend/cubeplex/plugins/protocols.py
"""Plugin Protocols + supporting dataclasses + version constant.

CUBEPLEX_PLUGIN_API_VERSION is the single integer plugins must declare via
their PluginManifest. Mismatch → registry refuses to load the plugin.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable
from uuid import UUID

from fastapi import APIRouter, Request

CUBEPLEX_PLUGIN_API_VERSION: Final[int] = 1


@dataclass(frozen=True)
class PluginManifest:
    """Plugin self-describing metadata. Required entry_point per wheel."""

    api_version: int
    name: str
    version: str
    description: str = ""
```

- [ ] **Step 4: Run test, verify pass**

Run: `cd backend && uv run pytest tests/plugins/test_protocols.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/plugins/protocols.py backend/tests/plugins/test_protocols.py
git commit -m "feat(plugins): add PluginManifest dataclass and CUBEPLEX_PLUGIN_API_VERSION constant"
```

---

### Task 3: Define `PermissionResource`, `AuditEvent`, `AdminNavItem`, `SyncResult`, `SyncSchedule` dataclasses

**Files:**
- Modify: `backend/cubeplex/plugins/protocols.py`
- Modify: `backend/tests/plugins/test_protocols.py`

- [ ] **Step 1: Add failing tests for new dataclasses**

Append to `backend/tests/plugins/test_protocols.py`:

```python
from datetime import UTC, datetime
from uuid import uuid4

from cubeplex.plugins.protocols import (
    AdminNavItem,
    AuditEvent,
    PermissionResource,
    SyncResult,
    SyncSchedule,
)


def test_permission_resource_minimal() -> None:
    r = PermissionResource(type="workspace", id=None)
    assert r.type == "workspace"
    assert r.id is None
    assert r.org_id is None
    assert r.workspace_id is None


def test_permission_resource_full() -> None:
    ws_id = uuid4()
    org_id = uuid4()
    r = PermissionResource(type="workspace", id=ws_id, org_id=org_id, workspace_id=ws_id)
    assert r.workspace_id == ws_id
    assert r.org_id == org_id


def test_audit_event_constructs() -> None:
    ev = AuditEvent(
        timestamp=datetime.now(UTC),
        user_id=uuid4(),
        org_id=uuid4(),
        workspace_id=None,
        action="auth.login",
        target_type=None,
        target_id=None,
        ip="127.0.0.1",
        user_agent="pytest",
        metadata={"foo": "bar"},
    )
    assert ev.action == "auth.login"
    assert ev.metadata == {"foo": "bar"}


def test_admin_nav_item_constructs() -> None:
    item = AdminNavItem(
        id="billing",
        label="Billing",
        icon="dollar",
        section="settings",
        order=10,
        url_path="billing/usage",
    )
    assert item.id == "billing"
    assert item.section == "settings"


def test_sync_result_defaults_to_zero_counts() -> None:
    r = SyncResult(added=0, updated=0, removed=0, errors=[])
    assert r.added == 0
    assert r.errors == []


def test_sync_schedule_allows_none_for_on_demand() -> None:
    s = SyncSchedule(interval_seconds=None)
    assert s.interval_seconds is None
```

- [ ] **Step 2: Run tests, verify failures**

Run: `cd backend && uv run pytest tests/plugins/test_protocols.py -v`
Expected: FAIL with import errors for PermissionResource, AuditEvent, AdminNavItem, SyncResult, SyncSchedule.

- [ ] **Step 3: Add dataclasses to protocols.py**

Append to `backend/cubeplex/plugins/protocols.py`:

```python
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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/plugins/test_protocols.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/plugins/protocols.py backend/tests/plugins/test_protocols.py
git commit -m "feat(plugins): add PermissionResource, AuditEvent, AdminNavItem, SyncResult, SyncSchedule"
```

---

### Task 4: Define 5 Protocols (`AuthProvider`, `PermissionChecker`, `AuditSink`, `UserDirectorySyncer`, `AdminPanelExtension`)

**Files:**
- Modify: `backend/cubeplex/plugins/protocols.py`
- Modify: `backend/tests/plugins/test_protocols.py`

- [ ] **Step 1: Add failing tests asserting Protocol satisfaction**

Append to `backend/tests/plugins/test_protocols.py`:

```python
from cubeplex.plugins.protocols import (
    AdminPanelExtension,
    AuditSink,
    AuthProvider,
    PermissionChecker,
    UserDirectorySyncer,
)


class _SatisfiesAuthProvider:
    async def authenticate(self, request):  # type: ignore[no-untyped-def]
        return None
    def get_auth_routers(self):  # type: ignore[no-untyped-def]
        return []


class _SatisfiesPermissionChecker:
    async def check(self, user, action, resource):  # type: ignore[no-untyped-def]
        return False


class _SatisfiesAuditSink:
    async def record(self, event):  # type: ignore[no-untyped-def]
        return None


class _SatisfiesUserDirectorySyncer:
    async def sync(self):  # type: ignore[no-untyped-def]
        return SyncResult(added=0, updated=0, removed=0, errors=[])
    def get_schedule(self):  # type: ignore[no-untyped-def]
        return SyncSchedule(interval_seconds=None)


class _SatisfiesAdminPanelExtension:
    def get_router(self):  # type: ignore[no-untyped-def]
        return None
    def get_nav_items(self):  # type: ignore[no-untyped-def]
        return []
    def get_static_path(self):  # type: ignore[no-untyped-def]
        return None


def test_protocols_are_runtime_checkable() -> None:
    assert isinstance(_SatisfiesAuthProvider(), AuthProvider)
    assert isinstance(_SatisfiesPermissionChecker(), PermissionChecker)
    assert isinstance(_SatisfiesAuditSink(), AuditSink)
    assert isinstance(_SatisfiesUserDirectorySyncer(), UserDirectorySyncer)
    assert isinstance(_SatisfiesAdminPanelExtension(), AdminPanelExtension)


class _MissingMethod:
    """Doesn't satisfy AuthProvider — only has authenticate, no get_auth_routers."""

    async def authenticate(self, request):  # type: ignore[no-untyped-def]
        return None


def test_protocol_rejects_incomplete_impl() -> None:
    assert not isinstance(_MissingMethod(), AuthProvider)
```

- [ ] **Step 2: Run tests, verify failures**

Run: `cd backend && uv run pytest tests/plugins/test_protocols.py -v`
Expected: FAIL — Protocols don't exist yet.

- [ ] **Step 3: Add Protocol definitions to protocols.py**

Append to `backend/cubeplex/plugins/protocols.py`:

```python
# Forward-declare User type; imported only for type checking to avoid cycles
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cubeplex.models import User


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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/plugins/test_protocols.py -v`
Expected: PASS (11 tests total)

- [ ] **Step 5: Re-export from `cubeplex/plugins/__init__.py`**

Replace `backend/cubeplex/plugins/__init__.py`:

```python
"""CE/EE plugin protocols + entry_points-based discovery."""

from cubeplex.plugins.protocols import (
    CUBEPLEX_PLUGIN_API_VERSION,
    AdminNavItem,
    AdminPanelExtension,
    AuditEvent,
    AuditSink,
    AuthProvider,
    PermissionChecker,
    PermissionResource,
    PluginManifest,
    SyncResult,
    SyncSchedule,
    UserDirectorySyncer,
)

__all__ = [
    "CUBEPLEX_PLUGIN_API_VERSION",
    "AdminNavItem",
    "AdminPanelExtension",
    "AuditEvent",
    "AuditSink",
    "AuthProvider",
    "PermissionChecker",
    "PermissionResource",
    "PluginManifest",
    "SyncResult",
    "SyncSchedule",
    "UserDirectorySyncer",
]
```

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/plugins/protocols.py backend/cubeplex/plugins/__init__.py backend/tests/plugins/test_protocols.py
git commit -m "feat(plugins): define 5 runtime_checkable Protocols (auth/permissions/audit/sync/admin)"
```

---

### Task 5: `PluginRegistry` skeleton + manifest discovery + version validation

**Files:**
- Create: `backend/cubeplex/plugins/registry.py`
- Create: `backend/tests/plugins/test_registry_manifest.py`

- [ ] **Step 1: Write failing test for manifest discovery + version mismatch**

```python
# backend/tests/plugins/test_registry_manifest.py
"""PluginRegistry manifest discovery + version validation."""

from unittest.mock import MagicMock, patch

import pytest

from cubeplex.plugins.protocols import CUBEPLEX_PLUGIN_API_VERSION, PluginManifest
from cubeplex.plugins.registry import PluginRegistry


def _ep(name: str, value, group: str = "cubeplex.plugin_manifest"):
    """Build a fake importlib.metadata.EntryPoint mock that load() returns `value`."""
    m = MagicMock()
    m.name = name
    m.group = group
    m.value = f"<fake>:{name}"
    m.load.return_value = value
    return m


@pytest.mark.asyncio
async def test_no_external_manifests_uses_defaults_only() -> None:
    reg = PluginRegistry()
    with patch("cubeplex.plugins.registry.importlib.metadata.entry_points") as mock_eps:
        mock_eps.return_value = []
        await reg.discover()
    # No external manifests → registry has no plugins beyond defaults.
    assert reg._manifests == {}


@pytest.mark.asyncio
async def test_valid_manifest_is_registered() -> None:
    manifest = PluginManifest(
        api_version=CUBEPLEX_PLUGIN_API_VERSION, name="ee", version="0.1.0"
    )
    reg = PluginRegistry()
    with patch("cubeplex.plugins.registry.importlib.metadata.entry_points") as mock_eps:
        mock_eps.side_effect = lambda group: (
            [_ep("main", manifest)] if group == "cubeplex.plugin_manifest" else []
        )
        await reg.discover()
    assert "ee" in reg._manifests
    assert reg._manifests["ee"].version == "0.1.0"


@pytest.mark.asyncio
async def test_version_mismatch_raises() -> None:
    bad_manifest = PluginManifest(api_version=999, name="ee", version="0.1.0")
    reg = PluginRegistry()
    with patch("cubeplex.plugins.registry.importlib.metadata.entry_points") as mock_eps:
        mock_eps.side_effect = lambda group: (
            [_ep("main", bad_manifest)] if group == "cubeplex.plugin_manifest" else []
        )
        with pytest.raises(RuntimeError, match="api_version"):
            await reg.discover()


@pytest.mark.asyncio
async def test_missing_manifest_for_plugin_with_entry_point_raises() -> None:
    """A wheel registers an AuthProvider but no plugin_manifest → reject."""
    fake_provider_cls = MagicMock()
    reg = PluginRegistry()
    with patch("cubeplex.plugins.registry.importlib.metadata.entry_points") as mock_eps:
        def by_group(group):
            if group == "cubeplex.auth_provider":
                ep = _ep("rogue", fake_provider_cls, group)
                ep.dist = MagicMock(name="rogue-pkg")
                return [ep]
            return []
        mock_eps.side_effect = by_group
        with pytest.raises(RuntimeError, match="missing.*manifest"):
            await reg.discover()
```

- [ ] **Step 2: Run tests, verify fail**

Run: `cd backend && uv run pytest tests/plugins/test_registry_manifest.py -v`
Expected: FAIL — `cubeplex.plugins.registry` doesn't exist.

- [ ] **Step 3: Implement registry.py with manifest discovery**

```python
# backend/cubeplex/plugins/registry.py
"""Plugin discovery + resolution."""

from __future__ import annotations

import importlib.metadata
import logging
from typing import TYPE_CHECKING

from cubeplex.plugins.protocols import (
    CUBEPLEX_PLUGIN_API_VERSION,
    AdminPanelExtension,
    AuditSink,
    AuthProvider,
    PermissionChecker,
    PluginManifest,
    UserDirectorySyncer,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Per-Protocol entry_points group names. External wheels publish here.
GROUP_MANIFEST = "cubeplex.plugin_manifest"
GROUP_AUTH = "cubeplex.auth_provider"
GROUP_PERMISSIONS = "cubeplex.permission_checker"
GROUP_AUDIT = "cubeplex.audit_sink"
GROUP_DIRECTORY = "cubeplex.user_directory_syncer"
GROUP_ADMIN_PANEL = "cubeplex.admin_panel_extension"

PROTOCOL_GROUPS: dict[str, type] = {
    GROUP_AUTH: AuthProvider,
    GROUP_PERMISSIONS: PermissionChecker,
    GROUP_AUDIT: AuditSink,
    GROUP_DIRECTORY: UserDirectorySyncer,
    GROUP_ADMIN_PANEL: AdminPanelExtension,
}

# Reserved entry_point name for CE built-in implementations. External plugins
# may not use this name.
RESERVED_NAME = "builtin"


class PluginRegistry:
    """Singleton-style holder of discovered plugin classes + CE defaults."""

    def __init__(self) -> None:
        self._manifests: dict[str, PluginManifest] = {}  # plugin_name → manifest
        self._candidates: dict[str, dict[str, type]] = {
            group: {} for group in PROTOCOL_GROUPS
        }  # group → {entry_point_name → impl class}

    async def discover(self) -> None:
        """Scan all entry_points + validate manifests + collect candidates."""
        manifest_eps = list(importlib.metadata.entry_points(group=GROUP_MANIFEST))
        # Each plugin_manifest entry_point loads to a PluginManifest instance.
        plugin_dist_to_manifest: dict[str, PluginManifest] = {}
        for ep in manifest_eps:
            manifest = ep.load()
            if not isinstance(manifest, PluginManifest):
                raise RuntimeError(
                    f"entry_point {ep.value} did not return a PluginManifest"
                )
            if manifest.api_version != CUBEPLEX_PLUGIN_API_VERSION:
                raise RuntimeError(
                    f"plugin {manifest.name!r}: api_version={manifest.api_version} "
                    f"but cubeplex CE requires api_version={CUBEPLEX_PLUGIN_API_VERSION}"
                )
            self._manifests[manifest.name] = manifest
            # Map dist name to manifest for cross-group lookup
            dist_name = self._dist_name(ep)
            if dist_name:
                plugin_dist_to_manifest[dist_name] = manifest
            logger.info(
                "registered plugin manifest: %s v%s (api=%d)",
                manifest.name,
                manifest.version,
                manifest.api_version,
            )

        # Walk per-Protocol groups; reject unknown plugins (no manifest)
        for group, _ in PROTOCOL_GROUPS.items():
            for ep in importlib.metadata.entry_points(group=group):
                if ep.name == RESERVED_NAME:
                    raise RuntimeError(
                        f"entry_point name {RESERVED_NAME!r} is reserved for CE; "
                        f"plugin {ep.value} cannot use it"
                    )
                dist_name = self._dist_name(ep)
                if dist_name and dist_name not in plugin_dist_to_manifest:
                    raise RuntimeError(
                        f"plugin {ep.value} (dist={dist_name}) is missing a "
                        f"{GROUP_MANIFEST} entry_point"
                    )
                self._candidates[group][ep.name] = ep.load()
                logger.info("registered candidate %s.%s = %s", group, ep.name, ep.value)

    @staticmethod
    def _dist_name(ep) -> str | None:  # type: ignore[no-untyped-def]
        try:
            return ep.dist.name if ep.dist else None
        except AttributeError:
            return None
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/plugins/test_registry_manifest.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/plugins/registry.py backend/tests/plugins/test_registry_manifest.py
git commit -m "feat(plugins): PluginRegistry discovery + manifest version validation"
```

---

### Task 6: Singular Protocol resolution (selected sentinel + 0/1/multiple external)

**Files:**
- Modify: `backend/cubeplex/plugins/registry.py`
- Create: `backend/tests/plugins/test_registry_singular.py`

- [ ] **Step 1: Write failing tests for singular resolution scenarios**

```python
# backend/tests/plugins/test_registry_singular.py
from unittest.mock import MagicMock

import pytest

from cubeplex.plugins.protocols import CUBEPLEX_PLUGIN_API_VERSION, PluginManifest
from cubeplex.plugins.registry import GROUP_AUTH, PluginRegistry


class _StubAuthProvider:
    name: str

    def __init__(self, name="external"):
        self.name = name

    async def authenticate(self, request):  # type: ignore[no-untyped-def]
        return None

    def get_auth_routers(self):  # type: ignore[no-untyped-def]
        return []


def _seed_registry(reg: PluginRegistry, candidates: dict[str, type]) -> None:
    reg._candidates[GROUP_AUTH] = dict(candidates)
    reg._manifests = {
        "ee": PluginManifest(api_version=CUBEPLEX_PLUGIN_API_VERSION, name="ee", version="0.1.0")
    }


def test_zero_external_uses_default() -> None:
    reg = PluginRegistry()
    default = _StubAuthProvider("default")
    chosen = reg.resolve_singular(GROUP_AUTH, default=default, selected=None)
    assert chosen is default


def test_one_external_replaces_default() -> None:
    reg = PluginRegistry()
    default = _StubAuthProvider("default")
    _seed_registry(reg, {"saml": _StubAuthProvider})
    chosen = reg.resolve_singular(GROUP_AUTH, default=default, selected=None)
    assert isinstance(chosen, _StubAuthProvider)
    assert chosen is not default


def test_multiple_external_with_no_selected_raises() -> None:
    reg = PluginRegistry()
    default = _StubAuthProvider("default")
    _seed_registry(reg, {"saml": _StubAuthProvider, "oidc": _StubAuthProvider})
    with pytest.raises(RuntimeError, match="multiple"):
        reg.resolve_singular(GROUP_AUTH, default=default, selected=None)


def test_selected_builtin_forces_default() -> None:
    reg = PluginRegistry()
    default = _StubAuthProvider("default")
    _seed_registry(reg, {"saml": _StubAuthProvider})
    chosen = reg.resolve_singular(GROUP_AUTH, default=default, selected="builtin")
    assert chosen is default


def test_selected_by_name_picks_specific() -> None:
    reg = PluginRegistry()
    default = _StubAuthProvider("default")
    _seed_registry(
        reg, {"saml": _StubAuthProvider, "oidc": _StubAuthProvider}
    )
    chosen = reg.resolve_singular(GROUP_AUTH, default=default, selected="saml")
    assert isinstance(chosen, _StubAuthProvider)


def test_selected_unknown_name_raises() -> None:
    reg = PluginRegistry()
    default = _StubAuthProvider("default")
    _seed_registry(reg, {"saml": _StubAuthProvider})
    with pytest.raises(RuntimeError, match="not registered"):
        reg.resolve_singular(GROUP_AUTH, default=default, selected="nonexistent")
```

- [ ] **Step 2: Run, verify fail (resolve_singular missing)**

Run: `cd backend && uv run pytest tests/plugins/test_registry_singular.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'resolve_singular'`

- [ ] **Step 3: Implement `resolve_singular` on PluginRegistry**

Append to `PluginRegistry` class in `backend/cubeplex/plugins/registry.py`:

```python
    def resolve_singular(
        self,
        group: str,
        *,
        default: object,
        selected: str | None,
    ) -> object:
        """Resolve a singular Protocol candidate; instantiate or pass through default.

        Resolution rules:
        - selected="builtin" → CE default (forces fallback even if externals present)
        - selected="<name>"  → look up that entry_point name; raise if missing
        - selected=None      → 0 ext: default; 1 ext: that one; ≥2 ext: RuntimeError
        """
        candidates = self._candidates[group]

        if selected == RESERVED_NAME:
            return default
        if selected is not None:
            if selected not in candidates:
                raise RuntimeError(
                    f"{group}: 'selected' is {selected!r} but no such entry_point is "
                    f"registered (available: {sorted(candidates)})"
                )
            return candidates[selected]()

        # selected is None — implicit rules
        if len(candidates) == 0:
            return default
        if len(candidates) == 1:
            (cls,) = candidates.values()
            return cls()
        raise RuntimeError(
            f"{group}: multiple entry_points registered ({sorted(candidates)}); "
            f"set plugins.{group.split('.')[1]}.selected = '<name>' to pick one"
        )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/plugins/test_registry_singular.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/plugins/registry.py backend/tests/plugins/test_registry_singular.py
git commit -m "feat(plugins): registry.resolve_singular with selected sentinel + conflict rules"
```

---

### Task 7: Multi-instance Protocol resolution + `disabled` config

**Files:**
- Modify: `backend/cubeplex/plugins/registry.py`
- Create: `backend/tests/plugins/test_registry_plural.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/plugins/test_registry_plural.py
from cubeplex.plugins.registry import GROUP_AUDIT, PluginRegistry


class _StubSink:
    name = "stub"

    async def record(self, event):  # type: ignore[no-untyped-def]
        return None


class _StubSink2:
    name = "stub2"

    async def record(self, event):  # type: ignore[no-untyped-def]
        return None


def _seed(reg: PluginRegistry, candidates: dict[str, type]) -> None:
    reg._candidates[GROUP_AUDIT] = dict(candidates)


def test_plural_with_no_external_returns_only_default() -> None:
    reg = PluginRegistry()
    default = _StubSink()
    out = reg.resolve_plural(GROUP_AUDIT, default=default, disabled=[])
    assert out == [default]


def test_plural_with_one_external_returns_default_plus_external() -> None:
    reg = PluginRegistry()
    default = _StubSink()
    _seed(reg, {"siem": _StubSink2})
    out = reg.resolve_plural(GROUP_AUDIT, default=default, disabled=[])
    assert default in out
    assert any(isinstance(o, _StubSink2) for o in out)
    assert len(out) == 2


def test_plural_disabled_builtin_excludes_default() -> None:
    reg = PluginRegistry()
    default = _StubSink()
    _seed(reg, {"siem": _StubSink2})
    out = reg.resolve_plural(GROUP_AUDIT, default=default, disabled=["builtin"])
    assert default not in out
    assert any(isinstance(o, _StubSink2) for o in out)
    assert len(out) == 1


def test_plural_disabled_external_excludes_it() -> None:
    reg = PluginRegistry()
    default = _StubSink()
    _seed(reg, {"siem": _StubSink2, "other": _StubSink})
    out = reg.resolve_plural(GROUP_AUDIT, default=default, disabled=["siem"])
    assert default in out
    assert not any(isinstance(o, _StubSink2) for o in out)


def test_plural_default_can_be_none() -> None:
    """For multi-instance protocols without a CE default (e.g. UserDirectorySyncer)."""
    reg = PluginRegistry()
    out = reg.resolve_plural(GROUP_AUDIT, default=None, disabled=[])
    assert out == []
```

- [ ] **Step 2: Run tests, verify fail**

Run: `cd backend && uv run pytest tests/plugins/test_registry_plural.py -v`
Expected: FAIL — `resolve_plural` missing.

- [ ] **Step 3: Implement `resolve_plural`**

Append to `PluginRegistry` class in `backend/cubeplex/plugins/registry.py`:

```python
    def resolve_plural(
        self,
        group: str,
        *,
        default: object | None,
        disabled: list[str],
    ) -> list[object]:
        """Resolve all candidates for a plural Protocol; honor `disabled` filter.

        - default: optional CE built-in instance (registered as RESERVED_NAME)
        - disabled: list of entry_point names to exclude (incl. RESERVED_NAME for default)
        """
        disabled_set = set(disabled)
        out: list[object] = []
        if default is not None and RESERVED_NAME not in disabled_set:
            out.append(default)
        for name, cls in self._candidates[group].items():
            if name in disabled_set:
                continue
            out.append(cls())
        return out
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/plugins/test_registry_plural.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/plugins/registry.py backend/tests/plugins/test_registry_plural.py
git commit -m "feat(plugins): registry.resolve_plural with disabled filter"
```

---

### Task 8: CE default `AuthProvider` (wraps fastapi-users)

**Files:**
- Create: `backend/cubeplex/plugins/defaults/auth.py`
- Create: `backend/tests/plugins/test_default_auth.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/plugins/test_default_auth.py
import pytest
from cubeplex.plugins import AuthProvider
from cubeplex.plugins.defaults.auth import DefaultAuthProvider


def test_default_auth_provider_satisfies_protocol() -> None:
    p = DefaultAuthProvider()
    assert isinstance(p, AuthProvider)


def test_default_auth_provider_returns_routers() -> None:
    p = DefaultAuthProvider()
    routers = p.get_auth_routers()
    assert isinstance(routers, list)
    # fastapi-users gives at least login/register routers; expect ≥2
    assert len(routers) >= 2
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/plugins/test_default_auth.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement DefaultAuthProvider**

```python
# backend/cubeplex/plugins/defaults/auth.py
"""CE default AuthProvider: wraps fastapi-users for cookie/JWT auth."""

from __future__ import annotations

from fastapi import APIRouter, Request

from cubeplex.auth.jwt import auth_backend
from cubeplex.auth.users import fastapi_users
from cubeplex.models import User


class DefaultAuthProvider:
    """CE default: cookie-based JWT via fastapi-users."""

    async def authenticate(self, request: Request) -> User | None:
        # Delegate to fastapi-users' current_user dependency machinery.
        # We instantiate it lazily; calling it directly is async.
        get_user = fastapi_users.current_user(active=True, optional=True)
        return await get_user(request)  # type: ignore[no-any-return,operator]

    def get_auth_routers(self) -> list[APIRouter]:
        return [
            fastapi_users.get_auth_router(auth_backend),
            fastapi_users.get_register_router(
                # Schema imports stay lazy to avoid circular imports at module load
                __import__("cubeplex.api.schemas.auth", fromlist=["UserRead"]).UserRead,
                __import__("cubeplex.api.schemas.auth", fromlist=["UserCreate"]).UserCreate,
            ),
            fastapi_users.get_users_router(
                __import__("cubeplex.api.schemas.auth", fromlist=["UserRead"]).UserRead,
                __import__("cubeplex.api.schemas.auth", fromlist=["UserUpdate"]).UserUpdate,
            ),
        ]
```

NOTE: actual imports for UserRead/UserCreate/UserUpdate may need adjustment based on real `cubeplex/api/schemas/auth.py` shape. If the `__import__` indirection feels brittle, refactor to direct imports once the file structure is verified.

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/plugins/test_default_auth.py -v`
Expected: PASS (2 tests). If schema imports fail, fix them per actual `api/schemas/` paths.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/plugins/defaults/auth.py backend/tests/plugins/test_default_auth.py
git commit -m "feat(plugins): DefaultAuthProvider wraps fastapi-users"
```

---

### Task 9: CE default `PermissionChecker` (wraps Role lookup)

**Files:**
- Create: `backend/cubeplex/plugins/defaults/permissions.py`
- Create: `backend/tests/plugins/test_default_permissions.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/plugins/test_default_permissions.py
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cubeplex.models import Role
from cubeplex.plugins import PermissionChecker, PermissionResource
from cubeplex.plugins.defaults.permissions import DefaultPermissionChecker


def test_default_permission_checker_satisfies_protocol() -> None:
    assert isinstance(DefaultPermissionChecker(), PermissionChecker)


@pytest.mark.asyncio
async def test_admin_access_grants_admin_role() -> None:
    repo = AsyncMock()
    repo.get_role = AsyncMock(return_value=Role.ADMIN)
    checker = DefaultPermissionChecker(membership_repo_factory=lambda _s: repo)
    user = MagicMock(id=str(uuid4()))
    ws_id = uuid4()
    res = PermissionResource(type="workspace", id=ws_id, workspace_id=ws_id)
    assert await checker.check(user, "admin_access", res) is True


@pytest.mark.asyncio
async def test_admin_access_denies_member_role() -> None:
    repo = AsyncMock()
    repo.get_role = AsyncMock(return_value=Role.MEMBER)
    checker = DefaultPermissionChecker(membership_repo_factory=lambda _s: repo)
    user = MagicMock(id=str(uuid4()))
    ws_id = uuid4()
    res = PermissionResource(type="workspace", id=ws_id, workspace_id=ws_id)
    assert await checker.check(user, "admin_access", res) is False


@pytest.mark.asyncio
async def test_member_access_grants_admin_or_member() -> None:
    repo = AsyncMock()
    repo.get_role = AsyncMock(return_value=Role.MEMBER)
    checker = DefaultPermissionChecker(membership_repo_factory=lambda _s: repo)
    user = MagicMock(id=str(uuid4()))
    ws_id = uuid4()
    res = PermissionResource(type="workspace", id=ws_id, workspace_id=ws_id)
    assert await checker.check(user, "member_access", res) is True


@pytest.mark.asyncio
async def test_unknown_action_denies() -> None:
    repo = AsyncMock()
    repo.get_role = AsyncMock(return_value=Role.ADMIN)
    checker = DefaultPermissionChecker(membership_repo_factory=lambda _s: repo)
    user = MagicMock(id=str(uuid4()))
    ws_id = uuid4()
    res = PermissionResource(type="workspace", id=ws_id, workspace_id=ws_id)
    assert await checker.check(user, "delete_workspace", res) is False
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/plugins/test_default_permissions.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement DefaultPermissionChecker**

```python
# backend/cubeplex/plugins/defaults/permissions.py
"""CE default PermissionChecker: wraps existing Membership.get_role lookup."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cubeplex.models import Role
from cubeplex.plugins.protocols import PermissionResource

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from cubeplex.repositories import MembershipRepository


class DefaultPermissionChecker:
    """Maps known actions to Role checks; unknown actions deny."""

    def __init__(
        self,
        membership_repo_factory: Callable[[Any], "MembershipRepository"] | None = None,
    ) -> None:
        # Allow injection for tests; default obtains a fresh repo from current session.
        self._repo_factory = membership_repo_factory

    async def check(
        self,
        user: Any,
        action: str,
        resource: PermissionResource,
    ) -> bool:
        if resource.workspace_id is None:
            return False
        repo = self._get_repo()
        role = await repo.get_role(user_id=user.id, workspace_id=str(resource.workspace_id))
        if role is None:
            return False
        if action == "admin_access":
            return role == Role.ADMIN
        if action == "member_access":
            return role in (Role.ADMIN, Role.MEMBER)
        return False

    def _get_repo(self) -> "MembershipRepository":
        if self._repo_factory is None:
            raise RuntimeError(
                "DefaultPermissionChecker requires a membership_repo_factory "
                "in production (FastAPI dependency injection)"
            )
        # In production this will be wired via FastAPI Depends; for tests it's injected.
        return self._repo_factory(None)
```

NOTE: The production wiring for `_repo_factory` happens in Task 14 when we replace `require_role` to inject a session-bound checker via FastAPI dependency.

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/plugins/test_default_permissions.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/plugins/defaults/permissions.py backend/tests/plugins/test_default_permissions.py
git commit -m "feat(plugins): DefaultPermissionChecker wraps Role lookup"
```

---

### Task 10: CE default `AuditSink` (no-op + structlog)

**Files:**
- Create: `backend/cubeplex/plugins/defaults/audit.py`
- Create: `backend/tests/plugins/test_default_audit.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/plugins/test_default_audit.py
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cubeplex.plugins import AuditEvent, AuditSink
from cubeplex.plugins.defaults.audit import DefaultAuditSink


def test_default_audit_sink_satisfies_protocol() -> None:
    assert isinstance(DefaultAuditSink(), AuditSink)


@pytest.mark.asyncio
async def test_default_audit_sink_logs_via_structlog(caplog: pytest.LogCaptureFixture) -> None:
    sink = DefaultAuditSink()
    event = AuditEvent(
        timestamp=datetime.now(UTC),
        user_id=uuid4(),
        org_id=uuid4(),
        workspace_id=None,
        action="auth.login",
        target_type=None,
        target_id=None,
        ip="127.0.0.1",
        user_agent="pytest",
        metadata={},
    )
    with caplog.at_level("INFO"):
        await sink.record(event)
    assert any("auth.login" in r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/plugins/test_default_audit.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement DefaultAuditSink**

```python
# backend/cubeplex/plugins/defaults/audit.py
"""CE default AuditSink: structlog INFO no-op (no DB write)."""

from __future__ import annotations

import logging

from cubeplex.plugins.protocols import AuditEvent

logger = logging.getLogger("cubeplex.audit")


class DefaultAuditSink:
    async def record(self, event: AuditEvent) -> None:
        logger.info(
            "audit.%s user=%s org=%s ws=%s target=%s/%s ip=%s",
            event.action,
            event.user_id,
            event.org_id,
            event.workspace_id,
            event.target_type,
            event.target_id,
            event.ip,
            extra={"audit_event": event},
        )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/plugins/test_default_audit.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/plugins/defaults/audit.py backend/tests/plugins/test_default_audit.py
git commit -m "feat(plugins): DefaultAuditSink logs via structlog (no DB write)"
```

---

### Task 11: CE default `AdminPanelExtension` (empty)

**Files:**
- Create: `backend/cubeplex/plugins/defaults/admin_panel.py`
- Create: `backend/tests/plugins/test_default_admin_panel.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/plugins/test_default_admin_panel.py
from cubeplex.plugins import AdminPanelExtension
from cubeplex.plugins.defaults.admin_panel import DefaultAdminPanelExtension


def test_default_admin_panel_satisfies_protocol() -> None:
    assert isinstance(DefaultAdminPanelExtension(), AdminPanelExtension)


def test_default_admin_panel_returns_empty() -> None:
    e = DefaultAdminPanelExtension()
    assert e.get_router() is None
    assert e.get_nav_items() == []
    assert e.get_static_path() is None
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/plugins/test_default_admin_panel.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement DefaultAdminPanelExtension**

```python
# backend/cubeplex/plugins/defaults/admin_panel.py
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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/plugins/test_default_admin_panel.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/plugins/defaults/admin_panel.py backend/tests/plugins/test_default_admin_panel.py
git commit -m "feat(plugins): DefaultAdminPanelExtension returns empty (CE contributes no extensions)"
```

---

### Task 12: Wire `PluginRegistry` getters + module-level singleton

**Files:**
- Modify: `backend/cubeplex/plugins/registry.py`
- Modify: `backend/cubeplex/plugins/__init__.py`
- Create: `backend/tests/plugins/test_registry_getters.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/plugins/test_registry_getters.py
import pytest

from cubeplex.plugins import (
    AdminPanelExtension,
    AuditSink,
    AuthProvider,
    PermissionChecker,
)
from cubeplex.plugins.registry import PluginRegistry


@pytest.mark.asyncio
async def test_get_auth_provider_falls_back_to_default() -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    assert isinstance(reg.get_auth_provider(), AuthProvider)


@pytest.mark.asyncio
async def test_get_permission_checker_falls_back_to_default() -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    assert isinstance(reg.get_permission_checker(), PermissionChecker)


@pytest.mark.asyncio
async def test_get_audit_sinks_returns_at_least_default() -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    sinks = reg.get_audit_sinks()
    assert len(sinks) >= 1
    assert all(isinstance(s, AuditSink) for s in sinks)


@pytest.mark.asyncio
async def test_get_admin_panel_extensions_empty_in_ce() -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    exts = reg.get_admin_panel_extensions()
    # CE-only: only the default returning empty everything
    assert all(isinstance(e, AdminPanelExtension) for e in exts)


@pytest.mark.asyncio
async def test_get_user_directory_syncers_empty_in_ce() -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    syncers = reg.get_user_directory_syncers()
    assert syncers == []  # No CE default for syncer
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/plugins/test_registry_getters.py -v`
Expected: FAIL — getters not implemented.

- [ ] **Step 3: Implement getters + bind_defaults + module singleton**

Append to `PluginRegistry` class in `backend/cubeplex/plugins/registry.py`:

```python
    # Resolved instances (set by bind_defaults after discover).
    _auth_provider: object | None = None
    _permission_checker: object | None = None
    _audit_sinks: list[object] | None = None
    _user_directory_syncers: list[object] | None = None
    _admin_panel_extensions: list[object] | None = None

    def bind_defaults(
        self,
        *,
        auth_default: object | None = None,
        permissions_default: object | None = None,
        audit_default: object | None = None,
        admin_panel_default: object | None = None,
        config: object | None = None,
    ) -> None:
        """Resolve every Protocol with the supplied defaults + applied config.

        Defaults default to lazy imports if None, so tests can call without args.
        `config` should be a config object exposing `plugins.<group>.selected`
        and `plugins.<group>.disabled` (None = empty list / None).
        """
        from cubeplex.plugins.defaults.admin_panel import DefaultAdminPanelExtension
        from cubeplex.plugins.defaults.audit import DefaultAuditSink
        from cubeplex.plugins.defaults.auth import DefaultAuthProvider
        from cubeplex.plugins.defaults.permissions import DefaultPermissionChecker

        auth_default = auth_default or DefaultAuthProvider()
        permissions_default = permissions_default or DefaultPermissionChecker()
        audit_default = audit_default or DefaultAuditSink()
        admin_panel_default = admin_panel_default or DefaultAdminPanelExtension()

        sel_auth = self._cfg(config, "auth_provider", "selected")
        sel_perm = self._cfg(config, "permission_checker", "selected")
        dis_audit = self._cfg(config, "audit_sink", "disabled") or []
        dis_dir = self._cfg(config, "user_directory_syncer", "disabled") or []
        dis_admin = self._cfg(config, "admin_panel_extension", "disabled") or []

        self._auth_provider = self.resolve_singular(
            GROUP_AUTH, default=auth_default, selected=sel_auth
        )
        self._permission_checker = self.resolve_singular(
            GROUP_PERMISSIONS, default=permissions_default, selected=sel_perm
        )
        self._audit_sinks = self.resolve_plural(
            GROUP_AUDIT, default=audit_default, disabled=dis_audit
        )
        self._user_directory_syncers = self.resolve_plural(
            GROUP_DIRECTORY, default=None, disabled=dis_dir
        )
        self._admin_panel_extensions = self.resolve_plural(
            GROUP_ADMIN_PANEL, default=admin_panel_default, disabled=dis_admin
        )

    @staticmethod
    def _cfg(config: object | None, group_name: str, key: str):  # type: ignore[no-untyped-def]
        if config is None:
            return None
        return getattr(getattr(getattr(config, "plugins", None), group_name, None), key, None)

    def get_auth_provider(self):  # type: ignore[no-untyped-def]
        if self._auth_provider is None:
            raise RuntimeError("call bind_defaults() first")
        return self._auth_provider

    def get_permission_checker(self):  # type: ignore[no-untyped-def]
        if self._permission_checker is None:
            raise RuntimeError("call bind_defaults() first")
        return self._permission_checker

    def get_audit_sinks(self):  # type: ignore[no-untyped-def]
        return self._audit_sinks or []

    def get_user_directory_syncers(self):  # type: ignore[no-untyped-def]
        return self._user_directory_syncers or []

    def get_admin_panel_extensions(self):  # type: ignore[no-untyped-def]
        return self._admin_panel_extensions or []


# Module-level singleton, populated by app startup.
_registry: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry


def reset_registry_for_tests() -> None:
    global _registry
    _registry = None
```

- [ ] **Step 4: Re-export getters from `cubeplex/plugins/__init__.py`**

Append to `backend/cubeplex/plugins/__init__.py` (inside __all__ + new imports):

```python
from cubeplex.plugins.registry import (
    PluginRegistry,
    get_registry,
    reset_registry_for_tests,
)
```

Add to `__all__`: `"PluginRegistry"`, `"get_registry"`, `"reset_registry_for_tests"`.

- [ ] **Step 5: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/plugins/ -v`
Expected: All plugin tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/plugins/registry.py backend/cubeplex/plugins/__init__.py backend/tests/plugins/test_registry_getters.py
git commit -m "feat(plugins): registry getters + bind_defaults + module-level singleton"
```

---

### Task 13: Add `plugins.*` config schema (pydantic + YAML)

**Files:**
- Modify: `backend/cubeplex/config.py`
- Modify: `backend/config.yaml`
- Modify: `backend/config.development.yaml`
- Modify: `backend/config.test.yaml`

- [ ] **Step 1: Read current `cubeplex/config.py` to find pydantic model location**

Run: `cd backend && head -80 cubeplex/config.py`

Identify the pydantic settings class (commonly `Settings` or similar).

- [ ] **Step 2: Add plugins schema**

If `config.py` uses pydantic Settings, append a nested model:

```python
# Append in backend/cubeplex/config.py near other section schemas

from pydantic import BaseModel


class _SingularPluginConfig(BaseModel):
    selected: str | None = None  # null / "builtin" / "<plugin_name>"


class _PluralPluginConfig(BaseModel):
    disabled: list[str] = []


class PluginsConfig(BaseModel):
    auth_provider: _SingularPluginConfig = _SingularPluginConfig()
    permission_checker: _SingularPluginConfig = _SingularPluginConfig()
    audit_sink: _PluralPluginConfig = _PluralPluginConfig()
    user_directory_syncer: _PluralPluginConfig = _PluralPluginConfig()
    admin_panel_extension: _PluralPluginConfig = _PluralPluginConfig()


# Then add `plugins: PluginsConfig = PluginsConfig()` to the top-level Settings model.
```

If `config.py` uses dynaconf, expose `config.plugins` accessors via `config.get("plugins.auth_provider.selected")` style — and add the section to YAML files for default values.

- [ ] **Step 3: Append `plugins:` section to YAML configs**

To `backend/config.yaml`, `backend/config.development.yaml`, `backend/config.test.yaml`:

```yaml
plugins:
  auth_provider:
    selected: null           # null / "builtin" / "<plugin_name>"
  permission_checker:
    selected: null
  audit_sink:
    disabled: []
  user_directory_syncer:
    disabled: []
  admin_panel_extension:
    disabled: []
```

- [ ] **Step 4: Verify config loads without error**

Run: `cd backend && uv run python -c "from cubeplex.config import config; print(config.get('plugins.auth_provider.selected'))"`
Expected: prints `None`.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/config.py backend/config.yaml backend/config.development.yaml backend/config.test.yaml
git commit -m "feat(plugins): add plugins.* config section with sensible defaults"
```

---

### Task 14: Rewrite `require_role` to call `PermissionChecker`

**Files:**
- Modify: `backend/cubeplex/auth/dependencies.py`
- Run regression: `backend/tests/test_rbac.py`

- [ ] **Step 1: Write a focused regression test that asserts behavior unchanged**

Run existing test FIRST to ensure baseline green:

```bash
cd backend && uv run pytest tests/test_rbac.py -v
```

Expected: PASS (baseline confirmed before refactor).

- [ ] **Step 2: Modify `require_role` to delegate to PermissionChecker**

Replace the body of `require_role` factory in `backend/cubeplex/auth/dependencies.py`:

```python
from cubeplex.plugins import PermissionResource, get_registry
from cubeplex.plugins.defaults.permissions import DefaultPermissionChecker


def _action_for_roles(allowed: tuple[Role, ...]) -> str:
    """Map allowed-role set → action name for PermissionChecker.

    {ADMIN}            → "admin_access"
    {ADMIN, MEMBER}    → "member_access"
    other combinations not yet supported (would be M1-E3 territory).
    """
    s = set(allowed)
    if s == {Role.ADMIN}:
        return "admin_access"
    if s == {Role.ADMIN, Role.MEMBER}:
        return "member_access"
    raise NotImplementedError(f"role set {s} has no mapped action")


def require_role(
    *allowed: Role,
) -> Callable[..., Awaitable[RequestContext]]:
    """Dependency factory: enforce permission via PermissionChecker."""

    action = _action_for_roles(allowed)

    async def _check(
        ctx: Annotated[RequestContext, Depends(request_context)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> RequestContext:
        # Construct a session-bound checker. CE default reads MembershipRepository.
        checker = get_registry().get_permission_checker()
        # CE default needs a repo factory bound at call time
        if isinstance(checker, DefaultPermissionChecker):
            from cubeplex.repositories import MembershipRepository
            checker._repo_factory = lambda _s: MembershipRepository(session)
        resource = PermissionResource(
            type="workspace", id=ctx.workspace_id, workspace_id=ctx.workspace_id  # type: ignore[arg-type]
        )
        if not await checker.check(ctx.user, action, resource):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: action={action}",
            )
        return ctx

    return _check
```

NOTE: `ctx.workspace_id` is a `str` in `RequestContext`; `PermissionResource.id` is `UUID | None`. If passing str works in your DB layer use it; otherwise wrap with `UUID(ctx.workspace_id)`.

- [ ] **Step 3: Bootstrap the registry once at module import (or app startup)**

If running tests, the registry singleton needs `bind_defaults()` to have been called at least once. Add to `backend/cubeplex/plugins/__init__.py`:

```python
def ensure_registry_bound() -> None:
    """Idempotent: call from app startup or test fixtures to seed defaults."""
    reg = get_registry()
    if reg._auth_provider is None:  # not yet bound
        reg.bind_defaults()
```

Add to `__all__`.

In `backend/tests/conftest.py` (or create one if absent), add an autouse fixture:

```python
import pytest

from cubeplex.plugins import ensure_registry_bound, reset_registry_for_tests


@pytest.fixture(autouse=True)
def _bind_plugin_registry():
    reset_registry_for_tests()
    ensure_registry_bound()
```

- [ ] **Step 4: Run rbac tests and full auth tests to confirm regression-free**

```bash
cd backend && uv run pytest tests/test_rbac.py -v
cd backend && uv run pytest tests/test_auth.py -v 2>/dev/null || echo "no test_auth.py"
cd backend && uv run pytest tests/ -v -k "rbac or auth"
```

Expected: PASS for all rbac + auth tests.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/auth/dependencies.py backend/cubeplex/plugins/__init__.py backend/tests/conftest.py
git commit -m "refactor(auth): rewrite require_role to call PermissionChecker (CE default = current Role lookup)"
```

---

### Task 15: Wire `current_active_user` to `AuthProvider.authenticate`

**Files:**
- Modify: `backend/cubeplex/auth/dependencies.py`
- Run regression: any auth e2e tests

- [ ] **Step 1: Confirm baseline auth tests pass**

```bash
cd backend && uv run pytest tests/ -v -k "auth"
```

Expected: PASS.

- [ ] **Step 2: Replace `current_active_user` with delegate to AuthProvider**

In `backend/cubeplex/auth/dependencies.py`, replace the line:

```python
current_active_user = fastapi_users.current_user(active=True)
```

With:

```python
from fastapi import Request


async def current_active_user(request: Request) -> User:
    """Resolve the active user via the configured AuthProvider.

    CE default = fastapi-users JWT cookie. EE plugins (e.g. SAML) override.
    """
    user = await get_registry().get_auth_provider().authenticate(request)
    if user is None or not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user
```

- [ ] **Step 3: Run auth tests; verify no regression**

```bash
cd backend && uv run pytest tests/ -v -k "auth or rbac"
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/auth/dependencies.py
git commit -m "refactor(auth): current_active_user delegates to AuthProvider.authenticate"
```

---

### Task 16: Mount `AuthProvider.get_auth_routers()` at app startup

**Files:**
- Modify: `backend/cubeplex/api/app.py`

- [ ] **Step 1: Read current `app.py` to find where auth routers are mounted today**

```bash
cd backend && grep -n "auth" cubeplex/api/app.py | head -30
```

Identify lines like `app.include_router(fastapi_users.get_auth_router(...))`.

- [ ] **Step 2: Replace direct `fastapi_users.get_*_router` calls with registry-driven mount**

In `backend/cubeplex/api/app.py`, find the `lifespan` async context manager (or app startup section). Add at startup:

```python
from cubeplex.plugins import ensure_registry_bound, get_registry

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing lifespan setup (DB, MCP, etc.) ...
    reg = get_registry()
    await reg.discover()
    reg.bind_defaults(config=config)

    # Mount AuthProvider routers dynamically
    for router in reg.get_auth_provider().get_auth_routers():
        app.include_router(router, prefix="/api/v1/auth", tags=["auth"])

    yield
    # ... shutdown ...
```

REMOVE the static `app.include_router(fastapi_users.get_auth_router(...))` calls.

- [ ] **Step 3: Run e2e auth tests + manually start server**

```bash
cd backend && uv run pytest tests/ -v -k "auth"
cd backend && uv run python main.py &
sleep 3
curl -s http://localhost:8000/api/v1/auth/me  # should 401 without cookie
kill %1
```

Expected: tests PASS; server returns 401 on `/me` without cookie.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/api/app.py
git commit -m "feat(api): mount AuthProvider.get_auth_routers() at startup via registry"
```

---

### Task 17: Add `audit_log()` helper

**Files:**
- Create: `backend/cubeplex/plugins/audit.py`
- Create: `backend/tests/plugins/test_audit_helper.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/plugins/test_audit_helper.py
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from cubeplex.plugins import get_registry
from cubeplex.plugins.audit import audit_log


@pytest.mark.asyncio
async def test_audit_log_dispatches_to_all_sinks() -> None:
    sink_a = AsyncMock()
    sink_b = AsyncMock()
    reg = get_registry()
    reg._audit_sinks = [sink_a, sink_b]

    await audit_log(
        action="auth.login",
        user_id=uuid4(),
        org_id=uuid4(),
        workspace_id=None,
        ip="127.0.0.1",
    )
    sink_a.record.assert_awaited_once()
    sink_b.record.assert_awaited_once()
    event = sink_a.record.call_args.args[0]
    assert event.action == "auth.login"
    assert isinstance(event.timestamp, datetime)
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/plugins/test_audit_helper.py -v`
Expected: FAIL — `cubeplex.plugins.audit` doesn't exist.

- [ ] **Step 3: Implement audit helper**

```python
# backend/cubeplex/plugins/audit.py
"""Helper for emitting AuditEvents to all registered AuditSinks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cubeplex.plugins.protocols import AuditEvent
from cubeplex.plugins.registry import get_registry


async def audit_log(
    action: str,
    *,
    user_id: UUID | None = None,
    org_id: UUID | None = None,
    workspace_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Construct AuditEvent + dispatch to every registered AuditSink."""
    event = AuditEvent(
        timestamp=datetime.now(UTC),
        user_id=user_id,
        org_id=org_id,
        workspace_id=workspace_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip=ip,
        user_agent=user_agent,
        metadata=metadata or {},
    )
    for sink in get_registry().get_audit_sinks():
        await sink.record(event)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/plugins/test_audit_helper.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/plugins/audit.py backend/tests/plugins/test_audit_helper.py
git commit -m "feat(plugins): add audit_log helper that fans out to all AuditSinks"
```

---

### Task 18: Hook `audit_log("auth.login")` in `UserManager.on_after_login`

**Files:**
- Modify: `backend/cubeplex/auth/users.py`
- Create: `backend/tests/test_audit_login.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_audit_login.py
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cubeplex.auth.users import UserManager
from cubeplex.plugins import get_registry


@pytest.mark.asyncio
async def test_on_after_login_emits_audit_event() -> None:
    sink = AsyncMock()
    reg = get_registry()
    reg._audit_sinks = [sink]

    user = MagicMock(id=str(uuid4()), email="a@b.com")
    request = MagicMock(client=MagicMock(host="1.2.3.4"), headers={"user-agent": "test"})
    user_db = MagicMock()
    mgr = UserManager(user_db)

    await mgr.on_after_login(user, request)
    sink.record.assert_awaited_once()
    ev = sink.record.call_args.args[0]
    assert ev.action == "auth.login"
    assert ev.ip == "1.2.3.4"
```

- [ ] **Step 2: Run, verify fail**

Run: `cd backend && uv run pytest tests/test_audit_login.py -v`
Expected: FAIL — `on_after_login` doesn't exist on UserManager.

- [ ] **Step 3: Add `on_after_login` to UserManager**

In `backend/cubeplex/auth/users.py`, add method to `UserManager` class:

```python
    async def on_after_login(
        self,
        user: User,
        request: Request | None = None,
    ) -> None:
        from cubeplex.plugins.audit import audit_log
        await audit_log(
            action="auth.login",
            user_id=user.id,
            ip=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
```

- [ ] **Step 4: Modify existing `on_after_register` to also emit audit event**

In the same `UserManager.on_after_register`, add at end (after the bootstrap try/except block, before setting `_default_workspace_id`):

```python
        from cubeplex.plugins.audit import audit_log
        await audit_log(
            action="auth.register",
            user_id=user.id,
            ip=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/test_audit_login.py tests/test_rbac.py -v`
Expected: PASS for new test + no regression in rbac.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/auth/users.py backend/tests/test_audit_login.py
git commit -m "feat(auth): emit audit_log for auth.login + auth.register"
```

---

### Task 19: Hook `audit_log("workspace.invite_created")` in invite endpoint

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/workspaces.py`
- Create: `backend/tests/test_audit_invite.py`

- [ ] **Step 1: Find the create-invite handler**

```bash
cd backend && grep -n "invite" cubeplex/api/routes/v1/workspaces.py
```

Identify the handler (likely `async def create_invite(...)`).

- [ ] **Step 2: Write failing test**

```python
# backend/tests/test_audit_invite.py
"""End-to-end: creating a workspace invite emits an audit event."""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from cubeplex.plugins import get_registry


@pytest.mark.asyncio
async def test_create_invite_emits_audit(client: AsyncClient, admin_user_cookie):
    sink = AsyncMock()
    reg = get_registry()
    reg._audit_sinks = [sink]

    resp = await client.post(
        "/api/v1/workspaces/some-ws-id/invites",
        json={"email": "newuser@example.com"},
        cookies=admin_user_cookie,
    )
    assert resp.status_code in (200, 201)
    sink.record.assert_awaited()
    actions = [c.args[0].action for c in sink.record.await_args_list]
    assert "workspace.invite_created" in actions
```

NOTE: Adjust `client` and `admin_user_cookie` fixtures to match existing test infrastructure (see `backend/tests/conftest.py`).

- [ ] **Step 3: Run, expected to fail**

Run: `cd backend && uv run pytest tests/test_audit_invite.py -v`
Expected: FAIL.

- [ ] **Step 4: Add `audit_log` call in invite handler**

In `backend/cubeplex/api/routes/v1/workspaces.py`, find the create-invite handler. After successful invite creation:

```python
from cubeplex.plugins.audit import audit_log

# inside handler, after invite is persisted:
await audit_log(
    action="workspace.invite_created",
    user_id=ctx.user.id,
    org_id=ctx.org_id,
    workspace_id=ctx.workspace_id,
    target_type="invite",
    target_id=str(invite.id),
    ip=request.client.host if request.client else None,
    metadata={"invitee_email": invite.email},
)
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/test_audit_invite.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/workspaces.py backend/tests/test_audit_invite.py
git commit -m "feat(workspaces): emit audit_log for workspace.invite_created"
```

---

### Task 20: AdminPanelExtension startup scan + manifest endpoint

**Files:**
- Create: `backend/cubeplex/api/routes/v1/admin_extensions.py`
- Modify: `backend/cubeplex/api/app.py`
- Create: `backend/tests/test_admin_extensions.py`

- [ ] **Step 1: Write failing test for empty manifest in CE**

```python
# backend/tests/test_admin_extensions.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_admin_manifest_empty_in_ce(client: AsyncClient, admin_user_cookie) -> None:
    resp = await client.get(
        "/api/v1/admin/_extensions/manifest",
        cookies=admin_user_cookie,
    )
    assert resp.status_code == 200
    data = resp.json()
    # CE-only deployment: no plugin extensions registered
    assert data == []
```

- [ ] **Step 2: Run, expected to fail (404)**

Run: `cd backend && uv run pytest tests/test_admin_extensions.py -v`
Expected: FAIL.

- [ ] **Step 3: Create the manifest endpoint**

```python
# backend/cubeplex/api/routes/v1/admin_extensions.py
"""GET /api/v1/admin/_extensions/manifest — aggregated nav items + iframe URLs."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from cubeplex.auth.dependencies import current_active_user
from cubeplex.models import User
from cubeplex.plugins import get_registry

router = APIRouter(prefix="/admin/_extensions", tags=["admin"])


@router.get("/manifest")
async def get_manifest(_user: User = Depends(current_active_user)) -> list[dict]:
    out: list[dict] = []
    for ext in get_registry().get_admin_panel_extensions():
        nav_items = ext.get_nav_items()
        if not nav_items:
            continue
        # We don't know the plugin name from the instance; use class module as proxy.
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
```

- [ ] **Step 4: Mount router + admin extension routers + static at startup**

In `backend/cubeplex/api/app.py` lifespan, after `bind_defaults`:

```python
from cubeplex.api.routes.v1 import admin_extensions
from fastapi.staticfiles import StaticFiles

# inside lifespan:
app.include_router(admin_extensions.router, prefix="/api/v1")

for ext in reg.get_admin_panel_extensions():
    plugin_name = type(ext).__module__.split(".")[0]
    if (router := ext.get_router()) is not None:
        app.include_router(
            router, prefix=f"/api/v1/admin/_extensions/{plugin_name}"
        )
    if (static_path := ext.get_static_path()) is not None:
        app.mount(
            f"/api/v1/admin/_extensions/{plugin_name}/static",
            StaticFiles(directory=str(static_path)),
        )
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/test_admin_extensions.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_extensions.py backend/cubeplex/api/app.py backend/tests/test_admin_extensions.py
git commit -m "feat(admin): manifest endpoint + dynamic mount of AdminPanelExtension routers/static"
```

---

### Task 21: Build the in-tree fake plugin fixture

**Files:**
- Create: `backend/tests/fixtures/fake_plugin/pyproject.toml`
- Create: `backend/tests/fixtures/fake_plugin/fake_plugin/__init__.py`
- Create: `backend/tests/fixtures/fake_plugin/fake_plugin/auth.py`
- Create: `backend/tests/fixtures/fake_plugin/fake_plugin/permissions.py`
- Create: `backend/tests/fixtures/fake_plugin/fake_plugin/audit.py`
- Create: `backend/tests/fixtures/fake_plugin/fake_plugin/directory.py`
- Create: `backend/tests/fixtures/fake_plugin/fake_plugin/admin_panel.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
# backend/tests/fixtures/fake_plugin/pyproject.toml
[project]
name = "fake-plugin"
version = "0.0.1"
description = "Fake cubeplex plugin used by Layer 1 contract tests."
requires-python = ">=3.12"
dependencies = []

[project.entry-points."cubeplex.plugin_manifest"]
main = "fake_plugin:MANIFEST"

[project.entry-points."cubeplex.auth_provider"]
fake = "fake_plugin.auth:FakeAuthProvider"

[project.entry-points."cubeplex.permission_checker"]
fake = "fake_plugin.permissions:FakePermissionChecker"

[project.entry-points."cubeplex.audit_sink"]
fake = "fake_plugin.audit:FakeAuditSink"

[project.entry-points."cubeplex.user_directory_syncer"]
fake = "fake_plugin.directory:FakeUserDirectorySyncer"

[project.entry-points."cubeplex.admin_panel_extension"]
fake = "fake_plugin.admin_panel:FakeAdminPanelExtension"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"
```

- [ ] **Step 2: Write `fake_plugin/__init__.py`**

```python
# backend/tests/fixtures/fake_plugin/fake_plugin/__init__.py
from cubeplex.plugins import CUBEPLEX_PLUGIN_API_VERSION, PluginManifest

MANIFEST = PluginManifest(
    api_version=CUBEPLEX_PLUGIN_API_VERSION,
    name="fake",
    version="0.0.1",
    description="Fixture plugin for Layer 1 contract tests.",
)
```

- [ ] **Step 3: Write 5 plugin impl files**

`fake_plugin/auth.py`:

```python
from cubeplex.plugins import AuthProvider


class FakeAuthProvider:
    async def authenticate(self, request):  # type: ignore[no-untyped-def]
        return None

    def get_auth_routers(self):  # type: ignore[no-untyped-def]
        return []
```

`fake_plugin/permissions.py`:

```python
class FakePermissionChecker:
    async def check(self, user, action, resource):  # type: ignore[no-untyped-def]
        return True  # always permit (test stub)
```

`fake_plugin/audit.py`:

```python
class FakeAuditSink:
    async def record(self, event):  # type: ignore[no-untyped-def]
        pass
```

`fake_plugin/directory.py`:

```python
from cubeplex.plugins import SyncResult, SyncSchedule


class FakeUserDirectorySyncer:
    async def sync(self):  # type: ignore[no-untyped-def]
        return SyncResult(added=0, updated=0, removed=0, errors=[])

    def get_schedule(self):  # type: ignore[no-untyped-def]
        return SyncSchedule(interval_seconds=None)
```

`fake_plugin/admin_panel.py`:

```python
from cubeplex.plugins import AdminNavItem


class FakeAdminPanelExtension:
    def get_router(self):  # type: ignore[no-untyped-def]
        return None

    def get_nav_items(self):  # type: ignore[no-untyped-def]
        return [
            AdminNavItem(
                id="fake-tab",
                label="Fake",
                icon=None,
                section="custom",
                order=999,
                url_path="fake",
            )
        ]

    def get_static_path(self):  # type: ignore[no-untyped-def]
        return None
```

- [ ] **Step 4: Verify fixture installs**

```bash
cd backend && uv pip install -e tests/fixtures/fake_plugin --no-deps
cd backend && uv run python -c "from fake_plugin import MANIFEST; print(MANIFEST)"
cd backend && uv pip uninstall -y fake-plugin
```

Expected: prints PluginManifest content; uninstall completes.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/fixtures/fake_plugin/
git commit -m "test(plugins): in-tree fake plugin fixture for Layer 1 contract tests"
```

---

### Task 22: Layer 1 contract tests (`test_contracts.py` with 11 assertions)

**Files:**
- Create: `backend/tests/plugins/test_contracts.py`

Each contract test installs the fake plugin temporarily, exercises one rule, then uninstalls.

- [ ] **Step 1: Write `test_contracts.py` with 11 assertions**

```python
# backend/tests/plugins/test_contracts.py
"""Layer 1 contract tests — exercise PluginRegistry against in-tree fake_plugin."""

from __future__ import annotations

import importlib
import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cubeplex.plugins import (
    CUBEPLEX_PLUGIN_API_VERSION,
    AuthProvider,
    PluginManifest,
    reset_registry_for_tests,
)
from cubeplex.plugins.registry import PluginRegistry

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "fake_plugin"


@pytest.fixture
def installed_fake_plugin():
    subprocess.run(
        ["uv", "pip", "install", "-e", str(FIXTURE_DIR), "--no-deps"],
        check=True,
        capture_output=True,
    )
    importlib.invalidate_caches()
    yield
    subprocess.run(
        ["uv", "pip", "uninstall", "-y", "fake-plugin"],
        check=True,
        capture_output=True,
    )
    importlib.invalidate_caches()
    sys.modules.pop("fake_plugin", None)
    reset_registry_for_tests()


@pytest.fixture
def fresh_registry():
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


# ──────────────────────── 11 assertions ────────────────────────


@pytest.mark.asyncio
async def test_discovery_finds_fake_plugin(installed_fake_plugin, fresh_registry) -> None:
    reg = PluginRegistry()
    await reg.discover()
    assert "fake" in reg._manifests


@pytest.mark.asyncio
async def test_singular_zero_external_uses_default(fresh_registry) -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    from cubeplex.plugins.defaults.auth import DefaultAuthProvider
    assert isinstance(reg.get_auth_provider(), DefaultAuthProvider)


@pytest.mark.asyncio
async def test_singular_one_external_replaces_default(installed_fake_plugin, fresh_registry) -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    from fake_plugin.auth import FakeAuthProvider
    assert isinstance(reg.get_auth_provider(), FakeAuthProvider)


@pytest.mark.asyncio
async def test_singular_selected_builtin_forces_default(installed_fake_plugin, fresh_registry) -> None:
    class _Cfg:
        class plugins:
            class auth_provider:
                selected = "builtin"
            class permission_checker:
                selected = None
            class audit_sink:
                disabled = []
            class user_directory_syncer:
                disabled = []
            class admin_panel_extension:
                disabled = []
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults(config=_Cfg())
    from cubeplex.plugins.defaults.auth import DefaultAuthProvider
    assert isinstance(reg.get_auth_provider(), DefaultAuthProvider)


@pytest.mark.asyncio
async def test_singular_selected_by_name(installed_fake_plugin, fresh_registry) -> None:
    class _Cfg:
        class plugins:
            class auth_provider:
                selected = "fake"
            class permission_checker:
                selected = None
            class audit_sink:
                disabled = []
            class user_directory_syncer:
                disabled = []
            class admin_panel_extension:
                disabled = []
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults(config=_Cfg())
    from fake_plugin.auth import FakeAuthProvider
    assert isinstance(reg.get_auth_provider(), FakeAuthProvider)


@pytest.mark.asyncio
async def test_singular_selected_not_found_fails(fresh_registry) -> None:
    class _Cfg:
        class plugins:
            class auth_provider:
                selected = "nonexistent"
            class permission_checker:
                selected = None
            class audit_sink:
                disabled = []
            class user_directory_syncer:
                disabled = []
            class admin_panel_extension:
                disabled = []
    reg = PluginRegistry()
    await reg.discover()
    with pytest.raises(RuntimeError, match="not registered"):
        reg.bind_defaults(config=_Cfg())


@pytest.mark.asyncio
async def test_plural_aggregates_default_plus_external(installed_fake_plugin, fresh_registry) -> None:
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults()
    sinks = reg.get_audit_sinks()
    from cubeplex.plugins.defaults.audit import DefaultAuditSink
    from fake_plugin.audit import FakeAuditSink
    types = {type(s) for s in sinks}
    assert DefaultAuditSink in types
    assert FakeAuditSink in types


@pytest.mark.asyncio
async def test_plural_disabled_filters_out(installed_fake_plugin, fresh_registry) -> None:
    class _Cfg:
        class plugins:
            class auth_provider:
                selected = None
            class permission_checker:
                selected = None
            class audit_sink:
                disabled = ["builtin"]
            class user_directory_syncer:
                disabled = []
            class admin_panel_extension:
                disabled = []
    reg = PluginRegistry()
    await reg.discover()
    reg.bind_defaults(config=_Cfg())
    sinks = reg.get_audit_sinks()
    from cubeplex.plugins.defaults.audit import DefaultAuditSink
    assert not any(isinstance(s, DefaultAuditSink) for s in sinks)


@pytest.mark.asyncio
async def test_missing_manifest_rejects_plugin(tmp_path, fresh_registry) -> None:
    """Manually install a wheel with an entry_point but no plugin_manifest → reject."""
    pkg = tmp_path / "rogue_pkg"
    pkg.mkdir()
    (pkg / "rogue").mkdir()
    (pkg / "rogue" / "__init__.py").write_text("class R: pass")
    (pkg / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'rogue'\n"
        "version = '0.0.1'\n"
        "requires-python = '>=3.12'\n"
        "[project.entry-points.\"cubeplex.auth_provider\"]\n"
        "rogue = 'rogue:R'\n"
        "[build-system]\n"
        "requires = ['setuptools>=61']\n"
        "build-backend = 'setuptools.build_meta'\n"
    )
    subprocess.run(
        ["uv", "pip", "install", "-e", str(pkg), "--no-deps"],
        check=True,
        capture_output=True,
    )
    importlib.invalidate_caches()
    try:
        reg = PluginRegistry()
        with pytest.raises(RuntimeError, match="missing.*manifest"):
            await reg.discover()
    finally:
        subprocess.run(
            ["uv", "pip", "uninstall", "-y", "rogue"],
            check=True,
            capture_output=True,
        )
        importlib.invalidate_caches()


@pytest.mark.asyncio
async def test_api_version_mismatch_rejects(tmp_path, fresh_registry) -> None:
    """Plugin manifest with mismatched api_version is rejected."""
    pkg = tmp_path / "old_plugin"
    pkg.mkdir()
    (pkg / "old_pkg").mkdir()
    (pkg / "old_pkg" / "__init__.py").write_text(
        "from cubeplex.plugins import PluginManifest\n"
        "MANIFEST = PluginManifest(api_version=999, name='old', version='0.0.1')\n"
    )
    (pkg / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'old-pkg'\n"
        "version = '0.0.1'\n"
        "requires-python = '>=3.12'\n"
        "[project.entry-points.\"cubeplex.plugin_manifest\"]\n"
        "main = 'old_pkg:MANIFEST'\n"
        "[build-system]\n"
        "requires = ['setuptools>=61']\n"
        "build-backend = 'setuptools.build_meta'\n"
    )
    subprocess.run(
        ["uv", "pip", "install", "-e", str(pkg), "--no-deps"],
        check=True,
        capture_output=True,
    )
    importlib.invalidate_caches()
    try:
        reg = PluginRegistry()
        with pytest.raises(RuntimeError, match="api_version"):
            await reg.discover()
    finally:
        subprocess.run(
            ["uv", "pip", "uninstall", "-y", "old-pkg"],
            check=True,
            capture_output=True,
        )
        importlib.invalidate_caches()


@pytest.mark.asyncio
async def test_external_plugin_named_builtin_rejected(tmp_path, fresh_registry) -> None:
    """External entry_point name 'builtin' is reserved."""
    pkg = tmp_path / "rsv"
    pkg.mkdir()
    (pkg / "rsv_pkg").mkdir()
    (pkg / "rsv_pkg" / "__init__.py").write_text(
        "from cubeplex.plugins import PluginManifest, CUBEPLEX_PLUGIN_API_VERSION\n"
        "MANIFEST = PluginManifest(api_version=CUBEPLEX_PLUGIN_API_VERSION, name='rsv', version='0.0.1')\n"
        "class A:\n"
        "    async def authenticate(self, r): return None\n"
        "    def get_auth_routers(self): return []\n"
    )
    (pkg / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'rsv-pkg'\n"
        "version = '0.0.1'\n"
        "requires-python = '>=3.12'\n"
        "[project.entry-points.\"cubeplex.plugin_manifest\"]\n"
        "main = 'rsv_pkg:MANIFEST'\n"
        "[project.entry-points.\"cubeplex.auth_provider\"]\n"
        "builtin = 'rsv_pkg:A'\n"
        "[build-system]\n"
        "requires = ['setuptools>=61']\n"
        "build-backend = 'setuptools.build_meta'\n"
    )
    subprocess.run(
        ["uv", "pip", "install", "-e", str(pkg), "--no-deps"],
        check=True,
        capture_output=True,
    )
    importlib.invalidate_caches()
    try:
        reg = PluginRegistry()
        with pytest.raises(RuntimeError, match="reserved"):
            await reg.discover()
    finally:
        subprocess.run(
            ["uv", "pip", "uninstall", "-y", "rsv-pkg"],
            check=True,
            capture_output=True,
        )
        importlib.invalidate_caches()
```

- [ ] **Step 2: Run all contract tests**

Run: `cd backend && uv run pytest tests/plugins/test_contracts.py -v`
Expected: PASS for all 11 tests.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/plugins/test_contracts.py
git commit -m "test(plugins): Layer 1 contract tests (11 assertions for discovery + resolution)"
```

---

### Task 23: CI workflow `test-ee-compat` placeholder job

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Read current ci.yml structure**

```bash
cd backend && cat ../.github/workflows/ci.yml | head -100
```

Identify where existing jobs are declared (e.g., `backend-check`, `frontend-check`, `e2e`).

- [ ] **Step 2: Append placeholder job**

Append to `.github/workflows/ci.yml`:

```yaml
  test-ee-compat:
    # Layer 1: in-repo contract tests (always run)
    # Layer 2: real cubeplex-ee integration is gated until the EE repo exists
    runs-on: ubuntu-latest
    needs: [backend-check]   # adjust to your existing job name
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - name: Install backend
        run: cd backend && uv sync --all-extras
      - name: Run plugin contract tests
        run: cd backend && uv run pytest tests/plugins/test_contracts.py -v

  test-ee-compat-cross-repo:
    # Layer 2 placeholder: enable once cubeplex/cubeplex-ee repo exists
    if: false
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { path: cubeplex }
      - uses: actions/checkout@v4
        with: { repository: cubeplex/cubeplex-ee, path: cubeplex-ee, token: ${{ secrets.EE_REPO_READ_TOKEN }} }
      - uses: astral-sh/setup-uv@v3
      - name: Install CE from working tree + EE
        run: |
          cd cubeplex-ee
          uv pip install -e ../cubeplex/backend
          uv pip install -e .
      - name: Run EE smoke tests
        run: cd cubeplex-ee && uv run pytest
```

- [ ] **Step 3: Validate workflow YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(plugins): add test-ee-compat job for Layer 1 contract tests + Layer 2 placeholder"
```

---

### Task 24: E2E — end-to-end plugin architecture path

Per CLAUDE.md project rule "Focus on E2E tests". Contract tests (Task 22) verify the registry surface in isolation; this task verifies real integration: discovery via entry_points, auth/permission delegation on a live FastAPI app, and the admin extensions manifest served through a real HTTP request.

**Files:**
- Create: `backend/tests/e2e/test_plugin_architecture_e2e.py`

- [ ] **Step 1: Write failing e2e**

```python
# backend/tests/e2e/test_plugin_architecture_e2e.py
"""E2E: plugin discovery + CE defaults + admin manifest via real HTTP."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.mark.e2e
def test_ce_defaults_load_and_serve(client: TestClient) -> None:
    """CE-only deployment: all Protocols resolve to builtin; app starts cleanly."""
    from cubeplex.plugins import get_registry

    reg = get_registry()
    assert reg.auth_provider is not None
    assert reg.permission_checker is not None
    assert reg.audit_sink is not None
    assert reg.user_directory_syncer is None  # no default CE directory syncer
    # get_admin_panel_extensions returns a list in the current contract.
    assert isinstance(reg.get_admin_panel_extensions(), list)


@pytest.mark.e2e
def test_admin_extensions_manifest_requires_admin(client: TestClient, admin_cookie: str, member_cookie: str) -> None:
    """Manifest endpoint enforces admin gating end-to-end through real middleware."""
    r = client.get("/api/v1/admin/_extensions/manifest")
    assert r.status_code == 401  # unauthenticated

    r = client.get("/api/v1/admin/_extensions/manifest", cookies={"access_token": member_cookie})
    assert r.status_code == 403  # non-admin

    r = client.get("/api/v1/admin/_extensions/manifest", cookies={"access_token": admin_cookie})
    assert r.status_code == 200
    assert r.json() == []  # CE-only: no external AdminPanelExtension installed


@pytest.mark.e2e
def test_login_emits_audit_event(client: TestClient, member_credentials: tuple[str, str], caplog) -> None:
    """AuditSink receives `auth.login` via the real login flow."""
    email, password = member_credentials
    with caplog.at_level("INFO", logger="cubeplex.audit"):
        r = client.post("/auth/jwt/login", data={"username": email, "password": password})
        assert r.status_code == 200
    assert any("auth.login" in rec.message for rec in caplog.records), (
        "audit sink did not record auth.login"
    )


@pytest.mark.e2e
def test_permission_check_denies_non_admin_on_workspace_admin_route(
    client: TestClient, member_cookie: str, workspace_id: str
) -> None:
    """require_admin → PermissionChecker.check → denies non-admin on admin-only route."""
    r = client.get(f"/api/v1/workspaces/{workspace_id}/admin", cookies={"access_token": member_cookie})
    assert r.status_code == 403
```

- [ ] **Step 2: Add fixtures if missing**

In `backend/tests/e2e/conftest.py` ensure the following fixtures exist and are reused (don't duplicate if already present in other e2e modules): `client`, `admin_cookie`, `member_cookie`, `member_credentials`, `workspace_id`.

- [ ] **Step 3: Run e2e**

```bash
cd backend && uv run pytest tests/e2e/test_plugin_architecture_e2e.py -v
```

Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_plugin_architecture_e2e.py backend/tests/e2e/conftest.py
git commit -m "test(plugins): e2e plugin architecture (CE defaults + admin manifest + audit + authz)"
```

---

### Task 25: Final integration smoke test

**Files:**
- Run all tests + start server

- [ ] **Step 1: Full backend test sweep**

```bash
cd backend && uv run pytest tests/ -v
```

Expected: ALL pass (including unmodified test_rbac, auth, etc.).

- [ ] **Step 2: Start server, hit endpoints**

```bash
cd backend && uv run python main.py &
sleep 5
curl -s -i http://localhost:8000/api/v1/admin/_extensions/manifest \
  -H "Cookie: <admin_jwt>" | head -20
kill %1
```

Expected:
- Admin extensions manifest returns `[]` (CE-only deployment)
- No exceptions on startup

- [ ] **Step 3: Verify mypy / ruff passes**

```bash
cd backend && make check
```

Expected: All green.

- [ ] **Step 4: Commit no-op (or push to feature branch)**

```bash
git status   # should be clean
```

If clean, M0 is implementation-complete; ready for branch merge / PR review.

---

## Self-Review Notes (planner ran)

- ✅ Spec coverage:
  - 5 Protocols → Tasks 4
  - manifest + version → Tasks 2, 5
  - registry resolution (singular + plural + selected/disabled) → Tasks 5, 6, 7, 12
  - 4 CE defaults → Tasks 8-11
  - PermissionChecker integration → Task 14
  - AuthProvider integration → Tasks 15-16
  - audit_log + 3 hooks → Tasks 17-19
  - AdminPanelExtension scan + manifest endpoint → Task 20
  - fake_plugin fixture + 11 contract tests → Tasks 21-22
  - CI placeholder → Task 23
- ✅ Existing test_rbac.py + auth tests must remain green (Tasks 14, 15 validate)
- ✅ All entry_point group names match: `cubeplex.plugin_manifest`, `cubeplex.auth_provider`, `cubeplex.permission_checker`, `cubeplex.audit_sink`, `cubeplex.user_directory_syncer`, `cubeplex.admin_panel_extension`
- ✅ Reserved name `"builtin"` consistent across resolve_singular / resolve_plural / contract test
- ✅ `CUBEPLEX_PLUGIN_API_VERSION = 1` consistent
- ⚠ DefaultAuthProvider's lazy schema import (`__import__`) may need refactor when implementer verifies actual `cubeplex.api.schemas.auth` paths. Flagged in Task 8.
- ⚠ Plugin Schema imports for fastapi-users (`UserRead`/`UserCreate`/`UserUpdate`) expected in `cubeplex.api.schemas.auth`; if missing, implementer may need to create them as part of Task 8.
