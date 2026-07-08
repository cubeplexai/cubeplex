"""Cross-scope install uniqueness (R1 / R2 / R3).

Pins the rule that **within an org**, no two active installs may share a
display name, server URL, or template — regardless of whether one is
org-scope and the other is workspace-scope. Before the rule landed the
LLM runtime would slap a ``_<install-id-tail>`` collision suffix onto
duplicate slugs (e.g. ``WebTools_Mkma__web_search``), which leaks into
tool-call card labels and breaks the frontend tool registry lookup.

Same-scope duplicates have always been forbidden by the partial unique
indexes. These tests cover the previously-allowed cross-scope case.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# R1 — name collision across scopes
# ---------------------------------------------------------------------------


async def test_org_install_blocks_workspace_install_with_same_name(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Custom org install named ``X`` → workspace install named ``X`` rejected."""
    client, ws_id = admin_client
    # 1. Create the org install (custom — no template)
    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "Shared Search",
            "server_url": "https://org.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 201, res.text

    # 2. Seed a template whose name will collide once installed.
    from tests.e2e.conftest import _seed_four_layer_template

    template_id = await _seed_four_layer_template(
        slug="shared-search-template",
        name="Shared Search",  # same display name as the org install above
        supported_auth_methods=["none"],
        default_credential_policy="none",
    )

    # 3. Workspace admin tries to install the template — should 409.
    res = await client.post(
        f"/api/v1/ws/{ws_id}/mcp/installs",
        json={
            "template_id": template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "install_already_exists"


async def test_workspace_install_blocks_org_install_with_same_name(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
) -> None:
    """Workspace install of ``X`` → admin custom install named ``X`` rejected."""
    client, ws_id = admin_client
    # 1. Install workspace-scope from a template (its name becomes the
    #    install's display name).
    res = await client.post(
        f"/api/v1/ws/{ws_id}/mcp/installs",
        json={
            "template_id": noauth_template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert res.status_code == 201, res.text
    workspace_install_name = res.json()["name"]

    # 2. Org admin tries a custom install with the same name.
    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": workspace_install_name,
            "server_url": "https://different.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "install_already_exists"


# ---------------------------------------------------------------------------
# R2 — server_url collision across scopes
# ---------------------------------------------------------------------------


async def test_org_install_blocks_workspace_install_with_same_url(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Org custom install at URL X → workspace install of any template
    pointing at the same URL is rejected (different name, same target)."""
    client, ws_id = admin_client
    shared_url = "https://shared-url.example.com/mcp"
    # 1. Org custom install at the shared URL
    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "OrgScope Same URL",
            "server_url": shared_url,
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 201, res.text

    # 2. Seed a template at the same URL but with a different display name.
    from tests.e2e.conftest import _seed_four_layer_template

    template_id = await _seed_four_layer_template(
        slug="same-url-template",
        name="WsScope Same URL",
        server_url=shared_url,
        supported_auth_methods=["none"],
        default_credential_policy="none",
    )

    # 3. Workspace install at the same URL → 409
    res = await client.post(
        f"/api/v1/ws/{ws_id}/mcp/installs",
        json={
            "template_id": template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "install_already_exists"


# ---------------------------------------------------------------------------
# R3 — template collision across scopes
# ---------------------------------------------------------------------------


async def test_org_install_allows_workspace_enable_from_same_template(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Org install of template T → workspace enablement of T reuses identity."""
    import secrets

    from tests.e2e.conftest import _seed_four_layer_template

    suffix = secrets.token_hex(4)
    template_id = await _seed_four_layer_template(
        slug=f"same-template-layering-{suffix}",
        name=f"Same Template Layering {suffix}",
        supported_auth_methods=["none"],
        default_credential_policy="none",
    )
    client, ws_id = admin_client
    # 1. Admin installs the template at org scope.
    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": template_id,
            "install_scope": "org",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 201, res.text
    connector_id = res.json()["connector_id"]

    # 2. Workspace admin enables the same template → same connector identity.
    res = await client.post(
        f"/api/v1/ws/{ws_id}/mcp/installs",
        json={
            "template_id": template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert res.status_code == 201, res.text
    assert res.json()["connector_id"] == connector_id


# ---------------------------------------------------------------------------
# Same-scope dup still rejected (regression guard for the existing rule)
# ---------------------------------------------------------------------------


async def test_org_custom_install_dup_same_scope_still_rejected(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ws = admin_client
    body = {
        "template_id": None,
        "install_scope": "org",
        "name": "DupSameScope",
        "server_url": "https://dup-same.example.com/mcp",
        "transport": "streamable_http",
        "auth_method": "none",
        "default_credential_policy": "none",
        "auto_enable": {"mode": "none"},
    }
    res = await client.post("/api/v1/admin/mcp/installs", json=body)
    assert res.status_code == 201, res.text
    res = await client.post("/api/v1/admin/mcp/installs", json=body)
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "install_already_exists"


# ---------------------------------------------------------------------------
# R1' — names that slugify to the same namespace key collide
# ---------------------------------------------------------------------------


async def test_names_that_slugify_to_same_namespace_collide(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """``cubepi_runtime._slugify_for_namespace`` maps any non-alphanumeric
    run to ``_``, so ``Web Tools`` and ``Web-Tools`` both produce slug
    ``Web_Tools``. A raw ``(org_id, name)`` index would treat them as
    distinct and let both rows live — the LLM-facing tool namespace
    would then collide. The uniqueness rule must be enforced on the
    canonical slug, not the raw display name.
    """
    client, _ws = admin_client
    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "Web Tools",
            "server_url": "https://slug-a.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 201, res.text

    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "Web-Tools",  # different chars, same slug as above
            "server_url": "https://slug-b.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "install_already_exists"


# ---------------------------------------------------------------------------
# PATCH must hit the same 409 path, not commit a colliding update
# ---------------------------------------------------------------------------


async def test_patch_install_to_colliding_name_returns_409(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Editing an active install's ``name`` to collide with another active
    install must surface as a clean 409 — not a 500 from the
    ``IntegrityError`` raised by the org-wide partial unique index.
    """
    client, _ws = admin_client
    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "Original",
            "server_url": "https://patch-a.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 201, res.text
    other_id = res.json()["install_id"]

    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "Renamable",
            "server_url": "https://patch-b.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 201, res.text
    renamable_id = res.json()["install_id"]
    assert renamable_id != other_id

    # Rename Renamable → "Original" — should be rejected.
    res = await client.patch(
        f"/api/v1/admin/mcp/installs/{renamable_id}",
        json={"name": "Original"},
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "install_already_exists"


async def test_patch_install_to_colliding_url_returns_409(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Same as above but for ``server_url`` (the R2 lens of the rule)."""
    client, _ws = admin_client
    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "PatchUrlA",
            "server_url": "https://patch-url-a.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 201, res.text

    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "PatchUrlB",
            "server_url": "https://patch-url-b.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 201, res.text
    target_id = res.json()["install_id"]

    res = await client.patch(
        f"/api/v1/admin/mcp/installs/{target_id}",
        json={"server_url": "https://patch-url-a.example.com/mcp"},
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "install_already_exists"


async def test_patch_install_self_rename_succeeds(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """A PATCH that keeps the same name (no-op rename) or changes to a
    free name must NOT trip the conflict check — only collisions with
    *other* installs should 409."""
    client, _ws = admin_client
    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "SelfRenameOK",
            "server_url": "https://self-rename.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 201, res.text
    iid = res.json()["install_id"]

    # Same name — no-op
    res = await client.patch(
        f"/api/v1/admin/mcp/installs/{iid}",
        json={"name": "SelfRenameOK"},
    )
    assert res.status_code == 200, res.text

    # Free name — should succeed
    res = await client.patch(
        f"/api/v1/admin/mcp/installs/{iid}",
        json={"name": "SelfRenameOK2"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "SelfRenameOK2"
