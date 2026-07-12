from datetime import UTC, datetime
from uuid import uuid4

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
