"""Unit test for GET /api/v1/ws/{workspace_id}/mcp/available.

Uses FastAPI's TestClient with dependency overrides. The route composes
its own ``compute_available_rows`` call from ``install_svc`` + ``template_svc``
internals; this test stubs those services' repos so the call returns one
org-install row that lacks a state row in this workspace.
"""

from typing import Any

from fastapi.testclient import TestClient

from cubebox.api.app import create_app
from cubebox.audit.sink import NoOpAuditSink
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.mcp.dependencies import (
    get_audit_sink,
    get_connector_template_service,
    get_ws_install_service,
)
from cubebox.models import Role, User


async def _fake_audit() -> Any:
    return NoOpAuditSink()


async def _fake_member() -> RequestContext:
    user = User(id="usr-1", email="x@example.com", hashed_password="x")
    return RequestContext(user=user, org_id="org-1", workspace_id="ws-1", role=Role.MEMBER)


class _FakeOrgInstall:
    id = "mcpco-1"
    template_id = "mctpl-1"
    install_scope = "org"
    workspace_id = None
    name = "Notion"
    server_url = "https://example.com/mcp"
    transport = "streamable_http"
    auth_method = "oauth"
    default_credential_policy = "org"
    auth_status = "authorized"
    discovery_status = "ok"
    install_state = "active"
    tools_cache: list[dict[str, Any]] = []
    tool_citations: dict[str, Any] = {}
    last_error = None
    auto_enroll_new_workspaces = False


def test_ws_available_lists_org_install_without_state_row() -> None:
    async def _fake_template_svc() -> Any:
        class _T:
            async def list_active(self) -> list[Any]:
                return []

        return _T()

    async def _fake_install_svc() -> Any:
        class _S:
            class _InstallRepo:
                async def list_org_installs(self) -> list[Any]:
                    return [_FakeOrgInstall()]

                async def list_workspace_installs(self, ws_id: str) -> list[Any]:
                    return []

            class _StateRepo:
                async def list_for_workspace(self, ws_id: str) -> list[Any]:
                    return []

            class _GrantRepo:
                async def get_for_connector_scope(
                    self,
                    *,
                    connector_id: str,
                    grant_scope: str,
                    workspace_id: str | None,
                    user_id: str | None,
                ) -> object | None:
                    assert connector_id == "mcpco-1"
                    if grant_scope == "org":
                        return object()
                    return None

            _install_repo = _InstallRepo()
            _state_repo = _StateRepo()
            _grant_repo = _GrantRepo()

            async def _connector_id_for_install(self, _install: Any) -> str:
                return "mcpco-1"

        return _S()

    app = create_app()
    app.dependency_overrides[get_audit_sink] = _fake_audit
    app.dependency_overrides[require_member] = _fake_member
    app.dependency_overrides[get_ws_install_service] = _fake_install_svc
    app.dependency_overrides[get_connector_template_service] = _fake_template_svc
    client = TestClient(app)

    res = client.get("/api/v1/ws/ws-1/mcp/available")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["source"] == "org_install"
    assert body["items"][0]["install"]["connector_id"] == "mcpco-1"
    assert body["items"][0]["reason"] == "no_state_row"
    assert body["items"][0]["credential_availability_by_scope"] == {
        "org": True,
        "workspace": False,
        "user": False,
    }
