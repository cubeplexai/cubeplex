"""E2E tests for MCP catalog API routes (Phase 3).

Covers the public surface added in this phase:

- ``GET  /api/v1/ws/{ws}/mcp/catalog`` (3.1)
- ``POST /api/v1/admin/mcp/catalog/{catalog_id}/install`` (3.2)
- ``DELETE /api/v1/admin/mcp/installs/{install_id}`` (3.3)
- ``PATCH  /api/v1/admin/mcp/installs/{install_id}`` (3.4)
- ``POST /api/v1/ws/{ws}/mcp/catalog/{catalog_id}/install`` (3.5)
- ``DELETE /api/v1/ws/{ws}/mcp/installs/{install_id}`` (3.7)
- handcrafted ``POST /admin/mcp/servers`` retained as advanced surface (3.8)

Tool discovery is monkeypatched to return success without hitting the
network — the catalog connectors point at real provider URLs that we
don't want to talk to from CI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import MCPCatalogConnector
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository

# ---------------------------------------------------------------------------
# Catalog seeding helpers
# ---------------------------------------------------------------------------


async def _seed_connector(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    provider: str,
    description: str = "",
    server_url: str | None = None,
    transport: str = "streamable_http",
    supported_auth_methods: list[str] | None = None,
    default_credential_scope: str = "org",
    static_form_fields: list[dict[str, Any]] | None = None,
    static_auth_header_template: str | None = "Bearer {token}",
    cred_metadata: dict[str, Any] | None = None,
    status: str = "active",
) -> MCPCatalogConnector:
    repo = MCPCatalogConnectorRepository(session)
    row = await repo.upsert_by_slug(
        slug=slug,
        name=name,
        description=description or f"{name} test connector.",
        provider=provider,
        server_url=server_url or f"https://{slug}.example.com/mcp",
        transport=transport,
        supported_auth_methods=supported_auth_methods or ["oauth", "static"],
        default_credential_scope=default_credential_scope,
        static_form_fields=static_form_fields,
        static_auth_header_template=static_auth_header_template,
        cred_metadata=cred_metadata,
        status=status,
    )
    await session.commit()
    return row


@pytest_asyncio.fixture
async def catalog_seeded(db_session: AsyncSession) -> AsyncIterator[dict[str, str]]:
    """Seed three connectors and yield ``{slug: connector_id}``.

    - ``github``: oauth + static (default scope: org)
    - ``notion``: oauth + static (default scope: org)
    - ``mslearn``: none only
    """
    github = await _seed_connector(
        db_session,
        slug="github",
        name="GitHub",
        provider="GitHub",
        description="GitHub MCP server: repos, issues, pull requests.",
        supported_auth_methods=["oauth", "static"],
        static_form_fields=[
            {"name": "token", "label": "API token", "secret": True},
        ],
        cred_metadata={"docs_url": "https://docs.github.com/"},
    )
    notion = await _seed_connector(
        db_session,
        slug="notion",
        name="Notion",
        provider="Notion",
        description="Notion MCP server.",
        supported_auth_methods=["oauth", "static"],
        static_form_fields=[
            {"name": "token", "label": "API token", "secret": True},
        ],
    )
    mslearn = await _seed_connector(
        db_session,
        slug="mslearn",
        name="Microsoft Learn",
        provider="Microsoft",
        supported_auth_methods=["none"],
        default_credential_scope="none",
        static_form_fields=None,
        static_auth_header_template=None,
    )
    yield {"github": github.id, "notion": notion.id, "mslearn": mslearn.id}


@pytest.fixture(autouse=True)
def _stub_discover_tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make tool discovery return success without hitting the network."""

    async def _ok(*_args: object, **_kwargs: object) -> tuple[bool, list[Any], None]:
        return True, [], None

    monkeypatch.setattr("cubebox.services.mcp_catalog.discover_tools", _ok)
    monkeypatch.setattr("cubebox.services.mcp.discover_tools", _ok)
    yield


# ---------------------------------------------------------------------------
# 3.1 — GET /api/v1/ws/{ws}/mcp/catalog
# ---------------------------------------------------------------------------


async def test_catalog_list_returns_active_connectors(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_seeded: dict[str, str],
) -> None:
    client, workspace_id = admin_client

    resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    assert resp.status_code == 200, resp.text

    items = resp.json()["items"]
    slugs = {item["slug"] for item in items}
    assert {"github", "notion", "mslearn"} <= slugs

    github = next(item for item in items if item["slug"] == "github")
    assert github["name"] == "GitHub"
    assert github["provider"] == "GitHub"
    assert github["supported_auth_methods"] == ["oauth", "static"]
    assert github["default_credential_scope"] == "org"
    assert github["status"] == "active"
    assert github["org_install_id"] is None
    assert github["user_install_id"] is None
    assert github["workspace_visible"] is False
    assert github["metadata"].get("docs_url") == "https://docs.github.com/"
    # Secret-bearing fields must NOT leak.
    assert "oauth_static_client_id" not in github
    assert "oauth_static_client_secret_credential_id" not in github


async def test_catalog_list_filters_by_q(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_seeded: dict[str, str],
) -> None:
    client, workspace_id = admin_client

    resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog", params={"q": "git"})
    assert resp.status_code == 200, resp.text
    slugs = {item["slug"] for item in resp.json()["items"]}
    assert "github" in slugs
    assert "notion" not in slugs


async def test_catalog_list_other_org_sees_no_installs(
    admin_client: tuple[httpx.AsyncClient, str],
    member_client_org_b: tuple[httpx.AsyncClient, str],
    catalog_seeded: dict[str, str],
) -> None:
    """Catalog is system-level (visible to every org), but install fields are scoped."""
    client_a, workspace_a = admin_client
    client_b, workspace_b = member_client_org_b

    install_resp = await client_a.post(
        f"/api/v1/admin/mcp/catalog/{catalog_seeded['github']}/install",
        json={
            "scope": "org",
            "auth_method": "static",
            "credential_plaintext": "ghp_test",
        },
    )
    assert install_resp.status_code == 201, install_resp.text

    # Org B sees the catalog connector but org_install_id is None for them.
    resp = await client_b.get(f"/api/v1/ws/{workspace_b}/mcp/catalog")
    assert resp.status_code == 200, resp.text
    items = {item["slug"]: item for item in resp.json()["items"]}
    assert "github" in items
    assert items["github"]["org_install_id"] is None
    assert items["github"]["user_install_id"] is None


# ---------------------------------------------------------------------------
# 3.2 — POST /api/v1/admin/mcp/catalog/{catalog_id}/install
# ---------------------------------------------------------------------------


async def test_admin_install_static_succeeds(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_seeded: dict[str, str],
) -> None:
    client, workspace_id = admin_client

    resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_seeded['github']}/install",
        json={
            "scope": "org",
            "auth_method": "static",
            "credential_plaintext": "ghp_test",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["requires_oauth"] is False
    assert body["authed"] is True
    install_id = body["install_id"]

    # Catalog list should now reflect org_install_id + workspace_visible=True.
    list_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    items = {item["slug"]: item for item in list_resp.json()["items"]}
    assert items["github"]["org_install_id"] == install_id
    assert items["github"]["workspace_visible"] is True


async def test_admin_install_duplicate_returns_409(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_seeded: dict[str, str],
) -> None:
    client, _workspace_id = admin_client

    first = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_seeded['github']}/install",
        json={
            "scope": "org",
            "auth_method": "static",
            "credential_plaintext": "ghp_test",
        },
    )
    assert first.status_code == 201, first.text

    dup = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_seeded['github']}/install",
        json={
            "scope": "org",
            "auth_method": "static",
            "credential_plaintext": "ghp_test_2",
        },
    )
    assert dup.status_code == 409
    assert "install already exists" in dup.json()["detail"]


async def test_admin_install_oauth_returns_requires_oauth(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_seeded: dict[str, str],
) -> None:
    client, _workspace_id = admin_client

    resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_seeded['github']}/install",
        json={"scope": "org", "auth_method": "oauth"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["requires_oauth"] is True
    assert body["authed"] is False


async def test_admin_install_unsupported_auth_method_returns_400(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_seeded: dict[str, str],
) -> None:
    client, _workspace_id = admin_client

    # mslearn supports only "none".
    resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_seeded['mslearn']}/install",
        json={
            "scope": "org",
            "auth_method": "static",
            "credential_plaintext": "noop",
        },
    )
    assert resp.status_code == 400, resp.text
    assert "supported_auth_methods" in resp.json()["detail"]


async def test_admin_install_unknown_catalog_returns_404(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _workspace_id = admin_client
    resp = await client.post(
        "/api/v1/admin/mcp/catalog/mctlg-nonexistent/install",
        json={"scope": "org", "auth_method": "none"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3.3 — DELETE /api/v1/admin/mcp/installs/{install_id}
# ---------------------------------------------------------------------------


async def test_admin_delete_install_soft_disables(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_seeded: dict[str, str],
) -> None:
    client, _workspace_id = admin_client

    install_resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_seeded['github']}/install",
        json={
            "scope": "org",
            "auth_method": "static",
            "credential_plaintext": "ghp_test",
        },
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["install_id"]

    delete_resp = await client.delete(f"/api/v1/admin/mcp/installs/{install_id}")
    assert delete_resp.status_code == 204

    # Server row should still exist, but authed=false now.
    detail_resp = await client.get(f"/api/v1/admin/mcp/servers/{install_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["authed"] is False


async def test_admin_delete_install_unknown_returns_404(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _workspace_id = admin_client
    resp = await client.delete("/api/v1/admin/mcp/installs/mcp-nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3.4 — PATCH /api/v1/admin/mcp/installs/{install_id}
# ---------------------------------------------------------------------------


async def test_admin_switch_static_to_oauth(
    admin_client: tuple[httpx.AsyncClient, str],
    catalog_seeded: dict[str, str],
) -> None:
    client, _workspace_id = admin_client

    install_resp = await client.post(
        f"/api/v1/admin/mcp/catalog/{catalog_seeded['github']}/install",
        json={
            "scope": "org",
            "auth_method": "static",
            "credential_plaintext": "ghp_test",
        },
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["install_id"]

    patch_resp = await client.patch(
        f"/api/v1/admin/mcp/installs/{install_id}",
        json={"auth_method": "oauth"},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["install_id"] == install_id
    assert body["requires_oauth"] is True
    assert body["authed"] is False


# ---------------------------------------------------------------------------
# 3.5 — POST /api/v1/ws/{ws}/mcp/catalog/{catalog_id}/install
# ---------------------------------------------------------------------------


async def test_workspace_user_install_static(
    member_client: tuple[httpx.AsyncClient, str],
    catalog_seeded: dict[str, str],
) -> None:
    client, workspace_id = member_client

    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/catalog/{catalog_seeded['notion']}/install",
        json={
            "auth_method": "static",
            "credential_plaintext": "secret_notion_key",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["requires_oauth"] is False
    assert body["authed"] is True
    install_id = body["install_id"]

    # Catalog list reflects user_install_id + workspace_visible=True.
    list_resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/catalog")
    items = {item["slug"]: item for item in list_resp.json()["items"]}
    assert items["notion"]["user_install_id"] == install_id
    assert items["notion"]["org_install_id"] is None
    assert items["notion"]["workspace_visible"] is True


# ---------------------------------------------------------------------------
# 3.7 — DELETE /api/v1/ws/{ws}/mcp/installs/{install_id}
# ---------------------------------------------------------------------------


async def test_workspace_install_delete_by_creator(
    member_client: tuple[httpx.AsyncClient, str],
    catalog_seeded: dict[str, str],
) -> None:
    client, workspace_id = member_client

    install_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/catalog/{catalog_seeded['notion']}/install",
        json={"auth_method": "static", "credential_plaintext": "x"},
    )
    assert install_resp.status_code == 201, install_resp.text
    install_id = install_resp.json()["install_id"]

    delete_resp = await client.delete(f"/api/v1/ws/{workspace_id}/mcp/installs/{install_id}")
    assert delete_resp.status_code == 204


async def test_workspace_install_delete_unknown_returns_404(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, workspace_id = member_client
    resp = await client.delete(f"/api/v1/ws/{workspace_id}/mcp/installs/mcp-nope")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3.8 — handcrafted /admin/mcp/servers retained
# ---------------------------------------------------------------------------


async def test_handcrafted_admin_create_still_works(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Phase 3.8: handcrafted POST /admin/mcp/servers stays functional.

    Frontend will collapse it into "advanced" UI in Phase 6 — for now
    no client-visible behavior change.
    """
    client, _workspace_id = admin_client
    resp = await client.post(
        "/api/v1/admin/mcp/servers",
        json={
            "name": "handcrafted-still-works",
            "server_url": "http://127.0.0.1:9/handcrafted-still-works",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
            "timeout": 1.0,
            "sse_read_timeout": 1.0,
        },
    )
    assert resp.status_code == 201, resp.text
