"""Connector / template name uniqueness in the template-centric model.

The old cross-scope uniqueness rules (R1/R2/R3 between org and workspace
install rows) no longer apply since connectors are always org-scope and
are identified by their template. The remaining invariants are:

- Two org-custom templates in the same org cannot share a name (slug collision).
- PATCH /installs/{id} (name-only patch) cannot collide with another connector name.
- Global templates are still visible (can't be deleted by org admins).

Tests whose subject was the removed install-create surface (POST /admin/mcp/installs
with custom template_id=None, cross-scope install collisions) are deleted here.
Their invariant was that the LLM runtime slot names don't collide; this is now
enforced at the template-slug layer when creating org-custom templates.
"""

from __future__ import annotations

import secrets

import httpx
import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Template name collision
# ---------------------------------------------------------------------------


async def test_org_custom_template_name_collision_rejected(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Two org-custom templates with the same name → 409 on the second."""
    client, _ws = admin_client
    suffix = secrets.token_hex(4)
    name = f"Unique Template {suffix}"

    first = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": name,
            "server_url": f"https://unique-a-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": name,  # same name → slug collision
            "server_url": f"https://unique-b-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "connector_name_conflict"


# ---------------------------------------------------------------------------
# PATCH connector name collision
# ---------------------------------------------------------------------------


async def test_patch_connector_to_colliding_name_returns_409(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """PATCH that renames connector A to connector B's name must 409."""
    client, _ws = admin_client
    suffix = secrets.token_hex(4)

    # Create two templates and distribute both to get connectors
    t1 = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"PatchName A {suffix}",
            "server_url": f"https://patch-name-a-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert t1.status_code == 201, t1.text
    tpl_a_id = t1.json()["template_id"]

    t2 = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"PatchName B {suffix}",
            "server_url": f"https://patch-name-b-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert t2.status_code == 201, t2.text
    tpl_b_id = t2.json()["template_id"]

    # Distribute both to get connector IDs
    d1 = await client.post(
        f"/api/v1/admin/mcp/templates/{tpl_a_id}/distribute",
        json={"enable_existing": False, "auto_enroll": False},
    )
    assert d1.status_code == 200, d1.text

    d2 = await client.post(
        f"/api/v1/admin/mcp/templates/{tpl_b_id}/distribute",
        json={"enable_existing": False, "auto_enroll": False},
    )
    assert d2.status_code == 200, d2.text
    connector_b_id = d2.json()["connector"]["connector_id"]

    # Try to rename connector B to "PatchName A {suffix}" → should 409
    patch_resp = await client.patch(
        f"/api/v1/admin/mcp/installs/{connector_b_id}",
        json={"name": f"PatchName A {suffix}"},
    )
    assert patch_resp.status_code == 409, patch_resp.text
    assert patch_resp.json()["detail"]["code"] == "install_already_exists"


async def test_patch_connector_self_rename_succeeds(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """PATCH a connector's name to itself (no-op) or a free name → 200."""
    client, _ws = admin_client
    suffix = secrets.token_hex(4)

    t1 = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"SelfRename {suffix}",
            "server_url": f"https://self-rename-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert t1.status_code == 201, t1.text
    tpl_id = t1.json()["template_id"]

    dist = await client.post(
        f"/api/v1/admin/mcp/templates/{tpl_id}/distribute",
        json={"enable_existing": False, "auto_enroll": False},
    )
    assert dist.status_code == 200, dist.text
    connector_id = dist.json()["connector"]["connector_id"]

    # Self-rename (no-op) → 200
    r1 = await client.patch(
        f"/api/v1/admin/mcp/installs/{connector_id}",
        json={"name": f"SelfRename {suffix}"},
    )
    assert r1.status_code == 200, r1.text

    # Free rename → 200
    r2 = await client.patch(
        f"/api/v1/admin/mcp/installs/{connector_id}",
        json={"name": f"SelfRename2 {suffix}"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["name"] == f"SelfRename2 {suffix}"
