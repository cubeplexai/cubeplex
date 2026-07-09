"""Light handler-level tests for the four-layer MCP routes.

These tests focus on the contract that **cannot** be expressed at the route
registration level (Task 4 of the MCP management plan):

* Cross-field validation on ``AdminCreateInstallIn`` schema-level — POST
  ``/admin/mcp/installs`` with ``credential_policy='none'`` +
  ``auth_method='static'`` must 422 before reaching the service.
* PATCH ``/admin/mcp/installs/{id}`` setting ``default_credential_policy``
  re-validates against the loaded install row at the service layer.
* PATCH ``/ws/{ws}/mcp/connectors/{id}/state`` from a non-admin member 403s
  (workspace-admin guard sits on ``/connectors``, not ``/installs``).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from cubebox.api.app import create_app
from cubebox.api.routes.v1.admin_mcp import _validate_install_policy_pairing
from cubebox.api.schemas.mcp import (
    AdminCreateInstallIn,
    PatchInstallIn,
    WorkspaceCreateInstallIn,
)
from cubebox.audit.sink import NoOpAuditSink
from cubebox.mcp.dependencies import (
    get_admin_install_service,
    get_admin_request_context,
    get_audit_sink,
    get_connector_template_service,
    get_ws_install_service,
)


def test_admin_create_install_static_with_no_policy_rejected_at_schema_layer() -> None:
    """``credential_policy='none'`` with ``auth_method='static'`` must 422."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as excinfo:
        AdminCreateInstallIn(
            template_id="mctpl-x",
            install_scope="org",
            auth_method="static",
            default_credential_policy="none",
        )
    assert "credential_policy" in str(excinfo.value) or "auth_method" in str(excinfo.value)


def test_admin_create_install_none_with_static_policy_rejected_at_schema_layer() -> None:
    """``auth_method='none'`` requires ``credential_policy='none'``."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AdminCreateInstallIn(
            template_id="mctpl-x",
            install_scope="org",
            auth_method="none",
            default_credential_policy="user",
        )


def test_admin_create_install_consistent_combinations_pass() -> None:
    # auth_method=none + credential_policy=none
    body = AdminCreateInstallIn(
        template_id="mctpl-x",
        install_scope="org",
        auth_method="none",
        default_credential_policy="none",
    )
    assert body.auth_method == "none"
    # auth_method=static + credential_policy=user
    body2 = AdminCreateInstallIn(
        template_id="mctpl-x",
        install_scope="org",
        auth_method="static",
        default_credential_policy="user",
    )
    assert body2.default_credential_policy == "user"


def test_patch_install_in_rejects_unknown_keys() -> None:
    """``extra='forbid'`` on PatchInstallIn keeps the surface narrow."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PatchInstallIn(unknown_field="oops")  # type: ignore[call-arg]


class _NullSession:
    """Stub the bits of ``AsyncSession`` the PATCH handler touches.

    The handler now runs the R1/R2/R3 cross-scope preflight before
    persisting; the preflight wraps the SELECT in
    ``session.no_autoflush`` so the dirty install row doesn't get
    flushed early. These fake-repo unit tests never hit a real session
    so the context manager is a plain no-op.
    """

    no_autoflush = __import__("contextlib").nullcontext()


async def _no_conflict(**_kwargs: Any) -> bool:
    """``MCPConnectorService._has_install_conflict`` stub for fake-repo tests."""
    return False


class _FakeInstall:
    """Minimal stand-in for MCPConnector used in pairing tests."""

    def __init__(self, auth_method: str) -> None:
        self.auth_method = auth_method


def test_validate_install_policy_pairing_rejects_none_on_static() -> None:
    """Service-level guard mirrors the schema-level rule for PATCH paths."""
    import fastapi

    install = _FakeInstall(auth_method="static")
    with pytest.raises(fastapi.HTTPException) as excinfo:
        _validate_install_policy_pairing(
            install=install,  # type: ignore[arg-type]
            requested_policy="none",
            field="default_credential_policy",
        )
    assert excinfo.value.status_code == 422
    body = excinfo.value.detail
    assert isinstance(body, list)
    assert body[0]["loc"] == ["body", "default_credential_policy"]


def test_validate_install_policy_pairing_rejects_user_on_none() -> None:
    import fastapi

    install = _FakeInstall(auth_method="none")
    with pytest.raises(fastapi.HTTPException) as excinfo:
        _validate_install_policy_pairing(
            install=install,  # type: ignore[arg-type]
            requested_policy="user",
            field="credential_policy",
        )
    assert excinfo.value.status_code == 422


def test_validate_install_policy_pairing_passes_consistent() -> None:
    # static + user is fine
    _validate_install_policy_pairing(
        install=_FakeInstall(auth_method="static"),  # type: ignore[arg-type]
        requested_policy="user",
        field="default_credential_policy",
    )
    # none + none is fine
    _validate_install_policy_pairing(
        install=_FakeInstall(auth_method="none"),  # type: ignore[arg-type]
        requested_policy="none",
        field="default_credential_policy",
    )


# --------------------------- TestClient-level checks --------------------------- #
# These tests exercise the FastAPI app via TestClient with DI overrides so the
# handler logic runs end-to-end (without a real DB / auth). They focus on the
# 422 surface — auth-layer 403s are exercised elsewhere via the auth dependency
# tests.


async def _fake_audit_sink() -> Any:
    return NoOpAuditSink()


def _make_app_with_overrides(overrides: dict[Any, Any]) -> Any:
    app = create_app()
    # Always stub the audit sink so handlers that record audit events don't
    # try to reach into app.state.audit_sink (which is only set during the
    # lifespan startup the TestClient bypasses).
    app.dependency_overrides[get_audit_sink] = _fake_audit_sink
    for dep, replacement in overrides.items():
        app.dependency_overrides[dep] = replacement
    return app


def test_post_admin_install_static_with_none_policy_returns_422() -> None:
    """422 emitted at the schema layer before reaching the install service."""
    # Stub the auth dependency so we don't need a real user.
    from cubebox.auth.context import RequestContext
    from cubebox.models import Role, User

    async def _fake_admin_ctx() -> RequestContext:
        user = User(id="usr-1", email="x@example.com", hashed_password="x")
        return RequestContext(user=user, org_id="org-1", workspace_id="", role=Role.ADMIN)

    async def _fake_template_svc() -> Any:
        class _S:
            async def get_active(self, tid: str) -> Any:
                raise AssertionError("schema-layer 422 should fire before service is called")

        return _S()

    async def _fake_install_svc() -> Any:
        class _S:
            async def create_from_template_for_org(self, **kwargs: Any) -> Any:
                raise AssertionError("schema-layer 422 should fire before service is called")

        return _S()

    # The create route now resolves session / backend / signer / token_mgr
    # so it can drive post-grant discovery; stub them with no-op sentinels
    # since the request body should 422 before any of them is touched.
    from cubebox.credentials.dependencies import get_encryption_backend
    from cubebox.db.session import get_session
    from cubebox.mcp.dependencies import (
        get_admin_oauth_token_manager,
        get_user_token_signer,
    )

    async def _fake_dep() -> Any:
        return object()

    app = _make_app_with_overrides(
        {
            get_admin_request_context: _fake_admin_ctx,
            get_connector_template_service: _fake_template_svc,
            get_admin_install_service: _fake_install_svc,
            get_session: _fake_dep,
            get_encryption_backend: _fake_dep,
            get_user_token_signer: _fake_dep,
            get_admin_oauth_token_manager: _fake_dep,
        }
    )

    client = TestClient(app)
    res = client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": "mctpl-x",
            "install_scope": "org",
            "auth_method": "static",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 422, res.text


def test_patch_workspace_state_from_non_admin_returns_403() -> None:
    """The state edit lives under /connectors and requires workspace admin.

    A non-admin member must get 403 — without the guard, ordinary members
    could enable/disable connectors or change credential policy. The guard
    is enforced via ``require_admin``; we stub it to fail so the test
    doesn't need a real auth backend.
    """
    import fastapi

    from cubebox.auth.dependencies import require_admin

    async def _fake_require_admin() -> Any:
        raise fastapi.HTTPException(
            status_code=403, detail="Permission denied: action=admin_access"
        )

    async def _fake_install_svc() -> Any:
        # We expect the guard to fire before the service is touched, but
        # FastAPI still resolves the dependency. Stub it to a harmless
        # object so the resolver doesn't crash on its transitive deps
        # (encryption backend / DB session / etc.).
        return object()

    app = _make_app_with_overrides(
        {
            require_admin: _fake_require_admin,
            get_ws_install_service: _fake_install_svc,
        }
    )

    client = TestClient(app)
    res = client.patch(
        "/api/v1/ws/ws-1/mcp/connectors/mcins-1/state",
        json={"enabled": False},
    )
    assert res.status_code == 403, res.text


class _FullFakeInstall:
    """Stand-in MCPConnector with every attribute ``_install_to_out`` reads.

    Used by the server_url_hash recompute test: the handler now calls
    ``server_url_hash(body.server_url)`` and writes it back to
    ``install.server_url_hash`` on the row, so the test needs to assert
    on the persisted value. Mirroring the model surface (rather than
    monkey-patching attributes ad-hoc) keeps the test resistant to
    future cosmetic field additions on the response DTO.
    """

    def __init__(self, *, install_id: str, server_url: str, server_url_hash: str) -> None:
        self.id = install_id
        self.template_id = "mctpl-x"
        self.install_scope = "org"
        self.workspace_id: str | None = None
        self.name = "test-install"
        self.server_url = server_url
        self.server_url_hash = server_url_hash
        self.transport = "streamable_http"
        self.auth_method = "static"
        self.default_credential_policy = "org"
        self.auth_status = "pending"
        self.discovery_status = "pending"
        self.install_state = "active"
        self.tools_cache: list[Any] = []
        self.tool_citations: dict[str, Any] = {}
        self.last_error: str | None = None
        self.auto_enroll_new_workspaces = False
        self.headers: dict[str, str] = {}


def test_patch_admin_install_server_url_change_recomputes_hash() -> None:
    """PATCH that mutates ``server_url`` must recompute ``server_url_hash``.

    The two columns back the partial unique indexes on
    ``mcp_connector_installs`` — letting ``server_url`` drift away from
    ``server_url_hash`` would mean the dedup check could be bypassed by
    "change the URL, hash stays". The handler derives the hash from the
    new URL inside the route so the persisted row stays internally
    consistent; client-supplied ``server_url_hash`` values (if any are
    ever added to the schema) MUST be ignored.

    The fake repo records the row passed to ``update`` so the test can
    inspect the persisted state — this is the pattern the rest of the
    file uses for handler-level assertions without a real DB session.
    """
    from cubebox.auth.context import RequestContext
    from cubebox.mcp._constants import server_url_hash
    from cubebox.models import Role, User

    async def _fake_admin_ctx() -> RequestContext:
        user = User(id="usr-1", email="x@example.com", hashed_password="x")
        return RequestContext(user=user, org_id="org-1", workspace_id="", role=Role.ADMIN)

    original_url = "https://old.example.com/mcp"
    new_url = "https://new.example.com/mcp"
    original_hash = server_url_hash(original_url)

    fake_install = _FullFakeInstall(
        install_id="mcins-1",
        server_url=original_url,
        server_url_hash=original_hash,
    )

    class _Repo:
        def __init__(self) -> None:
            self.updated_rows: list[Any] = []
            self.session = _NullSession()

        async def get(self, install_id: str) -> Any:  # noqa: ARG002
            return fake_install

        async def update(self, install: Any) -> Any:
            self.updated_rows.append(install)
            return install

    repo = _Repo()

    class _Svc:
        def __init__(self) -> None:
            self._install_repo = repo
            self._has_install_conflict = _no_conflict

        async def _connector_id_for_install(self, _install: Any) -> str:
            return "mcpco-test"

    async def _fake_install_svc() -> Any:
        return _Svc()

    app = _make_app_with_overrides(
        {
            get_admin_request_context: _fake_admin_ctx,
            get_admin_install_service: _fake_install_svc,
        }
    )

    client = TestClient(app)
    res = client.patch(
        "/api/v1/admin/mcp/installs/mcins-1",
        json={"server_url": new_url},
    )
    assert res.status_code == 200, res.text

    # The handler must have called update once with the new URL AND the
    # newly-derived hash, not the original.
    assert len(repo.updated_rows) == 1
    persisted = repo.updated_rows[0]
    assert persisted.server_url == new_url
    assert persisted.server_url_hash == server_url_hash(new_url)
    assert persisted.server_url_hash != original_hash


def test_patch_admin_install_server_url_unchanged_keeps_hash() -> None:
    """If the body ``server_url`` matches the row, do nothing to the hash.

    Guards against the recompute path getting eagerly triggered on
    every PATCH (which would be harmless arithmetically — the hash is
    deterministic — but obscures intent and makes audit-log diffing
    noisier than it needs to be).
    """
    from cubebox.auth.context import RequestContext
    from cubebox.mcp._constants import server_url_hash
    from cubebox.models import Role, User

    async def _fake_admin_ctx() -> RequestContext:
        user = User(id="usr-1", email="x@example.com", hashed_password="x")
        return RequestContext(user=user, org_id="org-1", workspace_id="", role=Role.ADMIN)

    url = "https://stable.example.com/mcp"
    original_hash = server_url_hash(url)
    # Inject a deliberately wrong hash so we can prove the handler did
    # not touch the field. If the recompute logic fires unconditionally,
    # this would get overwritten to ``server_url_hash(url)`` and the
    # assertion below would fail.
    fake_install = _FullFakeInstall(
        install_id="mcins-1",
        server_url=url,
        server_url_hash="sentinel-not-recomputed",
    )

    class _Repo:
        def __init__(self) -> None:
            self.updated_rows: list[Any] = []
            self.session = _NullSession()

        async def get(self, install_id: str) -> Any:  # noqa: ARG002
            return fake_install

        async def update(self, install: Any) -> Any:
            self.updated_rows.append(install)
            return install

    repo = _Repo()

    class _Svc:
        def __init__(self) -> None:
            self._install_repo = repo
            self._has_install_conflict = _no_conflict

        async def _connector_id_for_install(self, _install: Any) -> str:
            return "mcpco-test"

    async def _fake_install_svc() -> Any:
        return _Svc()

    app = _make_app_with_overrides(
        {
            get_admin_request_context: _fake_admin_ctx,
            get_admin_install_service: _fake_install_svc,
        }
    )

    client = TestClient(app)
    # PATCH carries the SAME url already on the row — recompute must not fire.
    res = client.patch(
        "/api/v1/admin/mcp/installs/mcins-1",
        json={"server_url": url, "name": "renamed"},
    )
    assert res.status_code == 200, res.text
    assert len(repo.updated_rows) == 1
    persisted = repo.updated_rows[0]
    assert persisted.server_url == url
    # Hash field is untouched (still the sentinel we seeded with).
    assert persisted.server_url_hash == "sentinel-not-recomputed"
    # ``server_url_hash(url)`` would be different; sanity check that
    # the test's premise (a sentinel that does NOT match the real hash)
    # holds.
    assert original_hash != "sentinel-not-recomputed"


def test_patch_admin_install_none_policy_on_static_install_returns_422() -> None:
    """PATCH path re-validates against the loaded row when body lacks auth_method."""
    from cubebox.auth.context import RequestContext
    from cubebox.models import Role, User

    async def _fake_admin_ctx() -> RequestContext:
        user = User(id="usr-1", email="x@example.com", hashed_password="x")
        return RequestContext(user=user, org_id="org-1", workspace_id="", role=Role.ADMIN)

    class _Repo:
        async def get(self, install_id: str) -> Any:
            return _FakeInstall(auth_method="static")

        async def update(self, install: Any) -> Any:  # noqa: ARG002
            raise AssertionError("update should not be called on rejected PATCH")

    class _Svc:
        def __init__(self) -> None:
            self._install_repo = _Repo()

    async def _fake_install_svc() -> Any:
        return _Svc()

    app = _make_app_with_overrides(
        {
            get_admin_request_context: _fake_admin_ctx,
            get_admin_install_service: _fake_install_svc,
        }
    )

    client = TestClient(app)
    res = client.patch(
        "/api/v1/admin/mcp/installs/mcins-1",
        json={"default_credential_policy": "none"},
    )
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    # The field error must point at the offending field.
    assert any(d.get("loc") == ["body", "default_credential_policy"] for d in detail), detail


# ---------------------------------------------------------------------------
# Workspace install schema: WorkspaceCreateInstallIn
# ---------------------------------------------------------------------------


def test_workspace_create_install_defaults_install_scope_to_workspace() -> None:
    """``install_scope`` is optional — defaults to ``"workspace"``."""
    body = WorkspaceCreateInstallIn(
        template_id="mctpl-x",
        auth_method="none",
        default_credential_policy="none",
    )
    assert body.install_scope == "workspace"


def test_workspace_create_install_rejects_install_scope_org() -> None:
    """The workspace schema must reject ``install_scope: "org"`` outright."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkspaceCreateInstallIn(  # type: ignore[call-arg]
            template_id="mctpl-x",
            install_scope="org",  # type: ignore[arg-type]
            auth_method="none",
            default_credential_policy="none",
        )


def test_workspace_create_install_static_with_none_policy_rejected() -> None:
    """Cross-field validator mirrors the admin schema for the workspace shape."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkspaceCreateInstallIn(
            template_id="mctpl-x",
            auth_method="static",
            default_credential_policy="none",
        )


def test_workspace_create_install_forbids_unknown_keys() -> None:
    """``extra='forbid'`` keeps the contract narrow."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkspaceCreateInstallIn(  # type: ignore[call-arg]
            template_id="mctpl-x",
            auth_method="none",
            default_credential_policy="none",
            unknown_field="oops",
        )


def _make_fake_install_for_workspace(template_id: str, workspace_id: str) -> Any:
    """Stand-in ConnectorWithIdentity with the surface the route reads."""
    from cubebox.services.mcp_installs import ConnectorWithIdentity

    class _Row:
        def __init__(self) -> None:
            self.id = "mcpco-ws-1"
            self.template_id = template_id
            self.install_scope = "workspace"
            self.workspace_id = workspace_id
            self.name = "test-install"
            self.server_url = "https://example.com/mcp"
            self.server_url_hash = "hash"
            self.transport = "streamable_http"
            self.auth_method = "none"
            self.default_credential_policy = "none"
            self.auth_status = "not_required"
            self.discovery_status = "pending"
            self.install_state = "active"
            self.tools_cache: list[Any] = []
            self.tool_citations: dict[str, Any] = {}
            self.last_error: str | None = None
            self.auto_enroll_new_workspaces = False
            self.headers: dict[str, str] = {}

    return ConnectorWithIdentity(connector=_Row(), connector_id="mcpco-ws-1")  # type: ignore[arg-type]


def test_post_workspace_install_with_workspace_scope_returns_201() -> None:
    """Happy path: explicit ``install_scope: "workspace"`` passes the route."""
    from cubebox.auth.context import RequestContext
    from cubebox.models import Role, User

    workspace_id = "ws-1"
    template_id = "mctpl-x"
    fake_install = _make_fake_install_for_workspace(template_id, workspace_id)

    async def _fake_admin_ctx() -> RequestContext:
        user = User(id="usr-1", email="x@example.com", hashed_password="x")
        return RequestContext(user=user, org_id="org-1", workspace_id=workspace_id, role=Role.ADMIN)

    async def _fake_template_svc() -> Any:
        class _S:
            async def get_active(self, tid: str) -> Any:
                class _T:
                    id = tid

                return _T()

        return _S()

    captured: dict[str, Any] = {}

    async def _fake_install_svc() -> Any:
        class _S:
            async def create_from_template_for_workspace(self, **kwargs: Any) -> Any:
                captured.update(kwargs)
                return fake_install

        return _S()

    from cubebox.auth.dependencies import require_admin

    app = _make_app_with_overrides(
        {
            require_admin: _fake_admin_ctx,
            get_connector_template_service: _fake_template_svc,
            get_ws_install_service: _fake_install_svc,
        }
    )

    client = TestClient(app)
    res = client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["install_scope"] == "workspace"
    assert body["workspace_id"] == workspace_id
    assert captured["workspace_id"] == workspace_id


def test_post_workspace_install_omitting_install_scope_returns_201() -> None:
    """``install_scope`` defaults to ``"workspace"`` so an omitted field still works."""
    from cubebox.auth.context import RequestContext
    from cubebox.auth.dependencies import require_admin
    from cubebox.models import Role, User

    workspace_id = "ws-1"
    template_id = "mctpl-x"
    fake_install = _make_fake_install_for_workspace(template_id, workspace_id)

    async def _fake_admin_ctx() -> RequestContext:
        user = User(id="usr-1", email="x@example.com", hashed_password="x")
        return RequestContext(user=user, org_id="org-1", workspace_id=workspace_id, role=Role.ADMIN)

    async def _fake_template_svc() -> Any:
        class _S:
            async def get_active(self, tid: str) -> Any:
                class _T:
                    id = tid

                return _T()

        return _S()

    async def _fake_install_svc() -> Any:
        class _S:
            async def create_from_template_for_workspace(self, **kwargs: Any) -> Any:  # noqa: ARG002
                return fake_install

        return _S()

    app = _make_app_with_overrides(
        {
            require_admin: _fake_admin_ctx,
            get_connector_template_service: _fake_template_svc,
            get_ws_install_service: _fake_install_svc,
        }
    )

    client = TestClient(app)
    res = client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": template_id,
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert res.status_code == 201, res.text


def test_post_workspace_install_with_org_scope_returns_422() -> None:
    """A workspace POST that smuggles ``install_scope: "org"`` is rejected by the schema."""
    from cubebox.auth.context import RequestContext
    from cubebox.auth.dependencies import require_admin
    from cubebox.models import Role, User

    workspace_id = "ws-1"

    async def _fake_admin_ctx() -> RequestContext:
        user = User(id="usr-1", email="x@example.com", hashed_password="x")
        return RequestContext(user=user, org_id="org-1", workspace_id=workspace_id, role=Role.ADMIN)

    async def _fake_template_svc() -> Any:
        class _S:
            async def get_active(self, tid: str) -> Any:  # noqa: ARG002
                raise AssertionError("schema-layer 422 should fire before service is called")

        return _S()

    async def _fake_install_svc() -> Any:
        class _S:
            async def create_from_template_for_workspace(self, **kwargs: Any) -> Any:  # noqa: ARG002
                raise AssertionError("schema-layer 422 should fire before service is called")

        return _S()

    app = _make_app_with_overrides(
        {
            require_admin: _fake_admin_ctx,
            get_connector_template_service: _fake_template_svc,
            get_ws_install_service: _fake_install_svc,
        }
    )

    client = TestClient(app)
    res = client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": "mctpl-x",
            "install_scope": "org",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert res.status_code == 422, res.text


def test_post_workspace_install_unsupported_auth_method_returns_400() -> None:
    """Service ValueError must surface as 400 with the message as ``code``.

    The schema accepts ``auth_method="none"`` + ``credential_policy="none"`` on
    its own (the cross-field validator only cares about the policy/auth pair).
    But the service additionally cross-checks the requested ``auth_method``
    against the template's ``supported_auth_methods``; mismatches raise
    ``ValueError("auth_method_not_supported_by_template")`` which the route
    must catch and map to 400 instead of letting it propagate as a 500.
    """
    from cubebox.auth.context import RequestContext
    from cubebox.auth.dependencies import require_admin
    from cubebox.models import Role, User

    workspace_id = "ws-1"
    template_id = "mctpl-static-only"

    async def _fake_admin_ctx() -> RequestContext:
        user = User(id="usr-1", email="x@example.com", hashed_password="x")
        return RequestContext(user=user, org_id="org-1", workspace_id=workspace_id, role=Role.ADMIN)

    async def _fake_template_svc() -> Any:
        class _S:
            async def get_active(self, tid: str) -> Any:
                class _T:
                    id = tid

                return _T()

        return _S()

    async def _fake_install_svc() -> Any:
        class _S:
            async def create_from_template_for_workspace(self, **kwargs: Any) -> Any:  # noqa: ARG002
                raise ValueError("auth_method_not_supported_by_template")

        return _S()

    app = _make_app_with_overrides(
        {
            require_admin: _fake_admin_ctx,
            get_connector_template_service: _fake_template_svc,
            get_ws_install_service: _fake_install_svc,
        }
    )

    client = TestClient(app)
    res = client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": template_id,
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert detail == {"code": "auth_method_not_supported_by_template"}, detail


def test_delete_workspace_install_uses_workspace_state_lookup() -> None:
    """Workspace DELETE should not depend on MCPConnector.workspace_id."""
    from cubebox.auth.context import RequestContext
    from cubebox.auth.dependencies import require_admin
    from cubebox.models import Role, User

    workspace_id = "ws-1"
    connector_id = "mcpco-ws-1"

    class _Connector:
        id = connector_id
        workspace_id = None

    class _ConnectorRepo:
        async def get(self, cid: str) -> Any:
            assert cid == connector_id
            return _Connector()

    class _StateRepo:
        async def get(self, ws_id: str, cid: str) -> Any:
            assert ws_id == workspace_id
            assert cid == connector_id
            return object()

    async def _fake_admin_ctx() -> RequestContext:
        user = User(id="usr-1", email="x@example.com", hashed_password="x")
        return RequestContext(user=user, org_id="org-1", workspace_id=workspace_id, role=Role.ADMIN)

    uninstalled: list[str] = []

    async def _fake_install_svc() -> Any:
        class _S:
            _install_repo = _ConnectorRepo()
            _state_repo = _StateRepo()

            async def uninstall(self, cid: str) -> Any:
                uninstalled.append(cid)
                return _Connector()

        return _S()

    app = _make_app_with_overrides(
        {
            require_admin: _fake_admin_ctx,
            get_ws_install_service: _fake_install_svc,
        }
    )

    client = TestClient(app)
    res = client.delete(f"/api/v1/ws/{workspace_id}/mcp/installs/{connector_id}")

    assert res.status_code == 204, res.text
    assert uninstalled == [connector_id]


def test_post_workspace_install_static_with_none_policy_returns_422() -> None:
    """Static auth + ``credential_policy="none"`` is rejected at the schema layer."""
    from cubebox.auth.context import RequestContext
    from cubebox.auth.dependencies import require_admin
    from cubebox.models import Role, User

    workspace_id = "ws-1"

    async def _fake_admin_ctx() -> RequestContext:
        user = User(id="usr-1", email="x@example.com", hashed_password="x")
        return RequestContext(user=user, org_id="org-1", workspace_id=workspace_id, role=Role.ADMIN)

    async def _fake_template_svc() -> Any:
        class _S:
            async def get_active(self, tid: str) -> Any:  # noqa: ARG002
                raise AssertionError("schema-layer 422 should fire before service is called")

        return _S()

    async def _fake_install_svc() -> Any:
        class _S:
            async def create_from_template_for_workspace(self, **kwargs: Any) -> Any:  # noqa: ARG002
                raise AssertionError("schema-layer 422 should fire before service is called")

        return _S()

    app = _make_app_with_overrides(
        {
            require_admin: _fake_admin_ctx,
            get_connector_template_service: _fake_template_svc,
            get_ws_install_service: _fake_install_svc,
        }
    )

    client = TestClient(app)
    res = client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": "mctpl-x",
            "auth_method": "static",
            "default_credential_policy": "none",
        },
    )
    assert res.status_code == 422, res.text
