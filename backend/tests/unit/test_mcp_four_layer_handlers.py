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
from cubebox.api.schemas.mcp import AdminCreateInstallIn, PatchInstallIn
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


class _FakeInstall:
    """Minimal stand-in for MCPConnectorInstall used in pairing tests."""

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

    app = _make_app_with_overrides(
        {
            get_admin_request_context: _fake_admin_ctx,
            get_connector_template_service: _fake_template_svc,
            get_admin_install_service: _fake_install_svc,
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
