"""E2E tests for catalog install → workspace runtime wiring (Phase 7.2).

Complements ``test_mcp_catalog_routes.py`` (CRUD surface) and
``test_mcp_catalog_override.py`` (override semantics) by exercising the
install → runtime path that an agent's tool loader observes:

- A successful catalog install populates ``tools_cache`` on the underlying
  ``MCPServer`` row, which the workspace ``GET /mcp/servers/{id}`` detail
  endpoint exposes (the agent runtime reads from the same row).
- Catalog listing's ``user_install_id`` and ``workspace_visible`` correctly
  scope per-user vs per-workspace.
- Workspace override on org A does not bleed into a parallel workspace.
- Deleting a static install clears the Credential vault row and zeroes
  ``tools_cache`` so subsequent agent loads see no stale tools.

Spec references: §11.2 (E2E coverage). Network is stubbed at
``cubebox.services.mcp_catalog.discover_tools`` — see ``stub_discover_tools``
in ``tests/e2e/conftest.py``. We override the stub locally where we need
non-empty ``tools_cache`` shape.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import Credential, MCPServer
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository
from tests.e2e.helpers import csrf_cookie_name as _csrf_cookie_name

# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


async def _seed_github(db_session: AsyncSession) -> str:
    repo = MCPCatalogConnectorRepository(db_session)
    row = await repo.upsert_by_slug(
        slug="github",
        name="GitHub",
        description="GitHub MCP server: repos, issues, pull requests.",
        provider="GitHub",
        server_url="https://github.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_scope="org",
        static_form_fields=[{"name": "token", "label": "API token", "secret": True}],
        static_auth_header_template="Bearer {token}",
    )
    await db_session.commit()
    return row.id


async def _seed_notion(db_session: AsyncSession) -> str:
    repo = MCPCatalogConnectorRepository(db_session)
    row = await repo.upsert_by_slug(
        slug="notion",
        name="Notion",
        description="Notion MCP server.",
        provider="Notion",
        server_url="https://notion.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_scope="org",
        static_form_fields=[{"name": "token", "label": "API token", "secret": True}],
        static_auth_header_template="Bearer {token}",
    )
    await db_session.commit()
    return row.id


@pytest_asyncio.fixture
async def github_catalog_id(db_session: AsyncSession) -> AsyncIterator[str]:
    yield await _seed_github(db_session)


@pytest_asyncio.fixture
async def notion_catalog_id(db_session: AsyncSession) -> AsyncIterator[str]:
    yield await _seed_notion(db_session)


@pytest.fixture
def stub_discover_with_tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Variant of ``stub_discover_tools`` that returns a non-empty tools list.

    Used by the wiring test so we can assert the tool name flowed through
    the install path into ``MCPServer.tools_cache`` (which is what the agent
    runtime ultimately reads).
    """

    fake_tool: dict[str, Any] = {
        "name": "github_list_repos",
        "description": "List GitHub repositories.",
        "input_schema": {"type": "object", "properties": {}},
    }

    async def _ok(
        *_args: object, **_kwargs: object
    ) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
        return True, [fake_tool], None

    monkeypatch.setattr("cubebox.services.mcp_catalog.discover_tools", _ok)
    monkeypatch.setattr("cubebox.services.mcp.discover_tools", _ok)
    yield


# ---------------------------------------------------------------------------
# 7.2 (a) — install → workspace runtime tools list
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_discover_with_tools")
async def test_org_install_appears_in_workspace_runtime_with_tools_cache(
    admin_client: tuple[httpx.AsyncClient, str],
    github_catalog_id: str,
) -> None:
    """Org admin static install → workspace mcp/servers detail exposes tools_cache.

    This is the high-value E2E that ties the catalog install path to the
    runtime tool-loading surface — the same ``MCPServer`` row the agent
    factory reads is exposed via ``GET /api/v1/ws/{ws}/mcp/servers/{id}``.
    """
    client, workspace_id = admin_client

    install_resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{github_catalog_id}/install",
        json={"auth_method": "static", "credential_plaintext": "ghp_test"},
    )
    assert install_resp.status_code == 201, install_resp.text
    body = install_resp.json()
    assert body["authed"] is True
    install_id = body["install_id"]

    # New semantics: org install is invisible by default. Enable it first.
    enable_resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/org-installs/{install_id}/override",
        json={"enabled": True},
    )
    assert enable_resp.status_code == 204, enable_resp.text

    # Org-wide install must show up in the workspace's inherited list.
    list_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers")
    assert list_resp.status_code == 200, list_resp.text
    inherited = {item["id"]: item for item in list_resp.json()["inherited"]}
    assert install_id in inherited
    assert inherited[install_id]["authed"] is True

    # Detail endpoint exposes tools_cache (include_tools_cache=True path).
    detail = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers/{install_id}")
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert detail_body["authed"] is True
    tools = detail_body["tools_cache"]
    assert isinstance(tools, list) and len(tools) == 1
    assert tools[0]["name"] == "github_list_repos"


# ---------------------------------------------------------------------------
# 7.2 (b) — workspace user catalog row scoping
# ---------------------------------------------------------------------------


async def _register(client: httpx.AsyncClient, email: str, password: str) -> None:
    r = await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    """Switch the shared client to a different user; returns a CSRF token.

    The csrf cookie name is per-worktree, so we resolve it via the shared
    helper. Header X-CSRF-Token must echo the cookie value for mutating
    requests after login.
    """
    client.cookies.clear()
    await client.get("/api/v1/auth/me")
    csrf = client.cookies.get(_csrf_cookie_name())
    assert csrf, "csrf cookie not set after GET /api/v1/auth/me"
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), r.text
    return client.cookies.get(_csrf_cookie_name()) or csrf


@pytest.mark.usefixtures("stub_discover_tools")
async def test_user_install_user_install_id_isolated_per_user(
    unauthenticated_memory_client: httpx.AsyncClient,
    notion_catalog_id: str,
) -> None:
    """Two members of the same workspace see distinct ``user_install_id`` values.

    User A installs the user-private workspace install. The catalog row for
    user A reports ``user_install_id == A's install``; the same row for
    user B (who has not installed) reports ``user_install_id is None``.

    This is the structural isolation guarantee for workspace-private
    self-installs — A's credential must never appear under B's name.
    """
    client = unauthenticated_memory_client
    a_email = f"runtime-a-{secrets.token_hex(4)}@example.com"
    b_email = f"runtime-b-{secrets.token_hex(4)}@example.com"
    pw = "passwordpassword"

    for email in (a_email, b_email):
        await _register(client, email, pw)

    # --- A: create shared workspace, invite B as member --------
    csrf_a = await _login(client, a_email, pw)
    r = await client.get("/api/v1/workspaces")
    assert r.status_code == 200
    a_org_id = r.json()[0]["org_id"]

    r = await client.post(
        "/api/v1/workspaces",
        json={"name": "Shared MCP Runtime", "org_id": a_org_id},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r.status_code == 201, r.text
    ws_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/invites",
        json={"role": "member"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r.status_code == 201, r.text
    invite_token = r.json()["token"]

    install_resp = await client.post(
        f"/api/v1/ws/{ws_id}/mcp/catalog/{notion_catalog_id}/install",
        json={"auth_method": "static", "credential_plaintext": "secret_a"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert install_resp.status_code == 201, install_resp.text
    a_install_id = install_resp.json()["install_id"]

    # A sees user_install_id pointing to their install.
    catalog_a = await client.get(f"/api/v1/ws/{ws_id}/mcp/catalog")
    assert catalog_a.status_code == 200
    items_a = {item["slug"]: item for item in catalog_a.json()["items"]}
    assert items_a["notion"]["user_install_id"] == a_install_id

    # --- B: accept invite, query the same catalog ---
    csrf_b = await _login(client, b_email, pw)
    r = await client.post(
        "/api/v1/workspaces/invites/accept",
        json={"token": invite_token},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert r.status_code == 200, r.text

    catalog_b = await client.get(f"/api/v1/ws/{ws_id}/mcp/catalog")
    assert catalog_b.status_code == 200
    items_b = {item["slug"]: item for item in catalog_b.json()["items"]}
    assert items_b["notion"]["user_install_id"] is None, (
        "B must not see A's user-private install under user_install_id"
    )


# ---------------------------------------------------------------------------
# 7.2 (c) — override hide is per-workspace
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_discover_tools")
async def test_workspace_override_disable_does_not_affect_other_workspace(
    admin_client: tuple[httpx.AsyncClient, str],
    github_catalog_id: str,
) -> None:
    """Disabling an org install in workspace A leaves workspace B unaffected.

    The existing ``test_mcp_catalog_override.py`` covers the same-workspace
    disable+re-enable cycle. This test pins the per-workspace boundary:
    one disable row must not bleed across workspaces.
    """
    client, workspace_a = admin_client

    # 1) Create a second workspace in the SAME org so the org-wide install is
    #    inherited by both.
    r = await client.get("/api/v1/workspaces")
    assert r.status_code == 200, r.text
    org_id = next(ws["org_id"] for ws in r.json() if ws["id"] == workspace_a)

    r = await client.post(
        "/api/v1/workspaces",
        json={"name": "Workspace B", "org_id": org_id},
    )
    assert r.status_code == 201, r.text
    workspace_b = r.json()["id"]

    # 2) Org-wide install via admin endpoint.
    install_resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{github_catalog_id}/install",
        json={"auth_method": "static", "credential_plaintext": "ghp_test"},
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["install_id"]

    # 3) Enable for both workspaces (org installs are invisible by default).
    for ws_id in (workspace_a, workspace_b):
        enable_resp = await client.patch(
            f"/api/v1/ws/{ws_id}/mcp/org-installs/{install_id}/override",
            json={"enabled": True},
        )
        assert enable_resp.status_code == 204, enable_resp.text

    for ws_id in (workspace_a, workspace_b):
        list_resp = await client.get(f"/api/v1/ws/{ws_id}/mcp/servers")
        assert list_resp.status_code == 200
        inherited = {item["id"] for item in list_resp.json()["inherited"]}
        assert install_id in inherited, f"{ws_id} should inherit org install"

    # 4) Disable in workspace A only (deletes the override row).
    disable_resp = await client.patch(
        f"/api/v1/ws/{workspace_a}/mcp/org-installs/{install_id}/override",
        json={"enabled": False},
    )
    assert disable_resp.status_code == 204, disable_resp.text

    # 5) Workspace A no longer inherits; workspace B still does.
    list_a = await client.get(f"/api/v1/ws/{workspace_a}/mcp/servers")
    inherited_a = {item["id"] for item in list_a.json()["inherited"]}
    assert install_id not in inherited_a

    list_b = await client.get(f"/api/v1/ws/{workspace_b}/mcp/servers")
    inherited_b = {item["id"] for item in list_b.json()["inherited"]}
    assert install_id in inherited_b, "override must not bleed across workspaces"


# ---------------------------------------------------------------------------
# 7.2 (d) — delete clears credential and tools_cache
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_discover_with_tools")
async def test_admin_delete_install_clears_credential_and_tools_cache(
    admin_client: tuple[httpx.AsyncClient, str],
    github_catalog_id: str,
    db_session: AsyncSession,
) -> None:
    """Soft-disable on delete: vault row gone, tools_cache emptied, authed=false.

    Subsequent agent loads observe a server row with no tools to wire.
    """
    client, _workspace_id = admin_client

    install_resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{github_catalog_id}/install",
        json={"auth_method": "static", "credential_plaintext": "ghp_test"},
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["install_id"]

    # Capture credential id BEFORE delete (the row reference is cleared by delete).
    server_before = await db_session.get(MCPServer, install_id)
    assert server_before is not None
    cred_id_before = server_before.credential_id
    assert cred_id_before is not None
    assert server_before.tools_cache, "stub should have populated tools_cache"

    # Delete via admin endpoint.
    delete_resp = await client.delete(f"/api/v1/admin/mcp/installs/{install_id}")
    assert delete_resp.status_code == 204

    # Server row still exists, but authed=false, tools_cache=[], credential_id=None.
    await db_session.refresh(server_before)
    assert server_before.authed is False
    assert server_before.tools_cache == []
    assert server_before.credential_id is None

    # Vault row deleted.
    cred_after = await db_session.get(Credential, cred_id_before)
    assert cred_after is None, "credential vault row must be hard-deleted on install delete"
