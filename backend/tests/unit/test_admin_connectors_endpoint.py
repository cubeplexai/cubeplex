"""Unit test for GET /api/v1/admin/mcp/connectors.

Uses FastAPI's TestClient with dependency overrides so the route can be
exercised without a DB / real session. Verifies response shape and that
the org_effective + workspace_distribution fields are populated.
"""

from typing import Any

from fastapi.testclient import TestClient

from cubebox.api.app import create_app
from cubebox.audit.sink import NoOpAuditSink
from cubebox.auth.context import RequestContext
from cubebox.mcp.dependencies import (
    get_admin_install_service,
    get_admin_request_context,
    get_audit_sink,
    get_grant_repo,
)
from cubebox.models import Role, User


async def _fake_audit_sink() -> Any:
    return NoOpAuditSink()


async def _fake_admin_ctx() -> RequestContext:
    user = User(id="usr-1", email="x@example.com", hashed_password="x")
    return RequestContext(user=user, org_id="org-1", workspace_id="", role=Role.ADMIN)


class _FakeInstall:
    def __init__(self, install_id: str, policy: str = "org") -> None:
        self.id = install_id
        self.template_id = "mctpl-1"
        self.install_scope = "org"
        self.workspace_id = None
        self.name = f"Install {install_id}"
        self.server_url = "https://example.com/mcp"
        self.server_url_hash = "abc"
        self.transport = "streamable_http"
        self.auth_method = "oauth"
        self.default_credential_policy = policy
        self.auth_status = "authorized"
        self.discovery_status = "ok"
        self.install_state = "active"
        self.tools_cache: list[dict[str, Any]] = []
        self.tool_citations: dict[str, Any] = {}
        self.last_error = None
        self.auto_enroll_new_workspaces = False
        self.org_id = "org-1"
        self.headers: dict[str, str] = {}
        self.timeout = 30.0
        self.sse_read_timeout = 30.0
        self.oauth_client_config: dict[str, Any] = {}


class _FakeSession:
    """Bare-minimum async session stub: every execute() returns an
    empty Result so list_for_install / list_for_org / template get
    don't blow up. The route just needs the calls to return empty
    sequences when there are no workspaces / states / templates
    for the fixture installs.
    """

    async def execute(self, _stmt: Any) -> Any:
        class _Result:
            def scalars(self) -> Any:
                class _Scalars:
                    def all(self) -> list[Any]:
                        return []

                    def first(self) -> Any:
                        return None

                return _Scalars()

            def scalar_one_or_none(self) -> Any:
                return None

        return _Result()


def test_admin_connectors_returns_one_row_per_org_install() -> None:
    async def _fake_install_svc() -> Any:
        class _S:
            class _Repo:
                session = _FakeSession()

                async def list_org_installs(self) -> list[Any]:
                    return [_FakeInstall("mcins-1"), _FakeInstall("mcins-2")]

            _install_repo = _Repo()

        return _S()

    async def _fake_grant_repo() -> Any:
        class _G:
            async def get_org_grant(self, install_id: str) -> Any:
                class _Grant:
                    grant_status = "valid"
                    refresh_credential_id = None

                return _Grant()

        return _G()

    app = create_app()
    app.dependency_overrides[get_audit_sink] = _fake_audit_sink
    app.dependency_overrides[get_admin_request_context] = _fake_admin_ctx
    app.dependency_overrides[get_admin_install_service] = _fake_install_svc
    app.dependency_overrides[get_grant_repo] = _fake_grant_repo
    client = TestClient(app)

    res = client.get("/api/v1/admin/mcp/connectors")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["org_effective"]["reason"] == "usable"
    assert body["items"][0]["workspace_distribution"]["eligible_count"] >= 0
