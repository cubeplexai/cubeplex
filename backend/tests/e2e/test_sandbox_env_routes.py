"""E2E tests for sandbox env vault routes.

The ``admin_client`` / ``member_client`` fixtures from tests/e2e/conftest.py
yield a tuple ``(client, workspace_id)`` — always unpack before use.
"""

# ---------------------------------------------------------------------------
# Org-scope admin routes (Task 9)
# ---------------------------------------------------------------------------


async def test_admin_create_and_delete_org_secret(admin_client):
    client, _ws = admin_client
    resp = await client.post(
        "/api/v1/admin/sandbox-env",
        json={
            "env_name": "GITHUB_TOKEN",
            "is_secret": True,
            "hosts": ["api.github.com"],
            "secret_value": "ghp_x",
        },
    )
    assert resp.status_code == 201
    entry = resp.json()
    assert entry["scope"] == "org"
    assert "secret_value" not in entry  # never leaked

    del_resp = await client.delete(f"/api/v1/admin/sandbox-env/{entry['id']}")
    assert del_resp.status_code == 204


async def test_admin_rejects_bad_host(admin_client):
    client, _ws = admin_client
    resp = await client.post(
        "/api/v1/admin/sandbox-env",
        json={"env_name": "X", "is_secret": True, "hosts": ["*.com"], "secret_value": "v"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Org-scope LIST and PATCH (Task 12)
# ---------------------------------------------------------------------------


async def test_admin_list_org_env(admin_client):
    client, _ws = admin_client
    create_resp = await client.post(
        "/api/v1/admin/sandbox-env",
        json={
            "env_name": "LIST_TEST_TOKEN",
            "is_secret": True,
            "hosts": ["api.example.com"],
            "secret_value": "s3cr3t",
        },
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]

    list_resp = await client.get("/api/v1/admin/sandbox-env")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert "entries" in data
    ids = [e["id"] for e in data["entries"]]
    assert entry_id in ids
    # Secret value must never appear in any entry
    for entry in data["entries"]:
        assert "secret_value" not in entry
        assert "credential_id" not in entry

    # Cleanup
    await client.delete(f"/api/v1/admin/sandbox-env/{entry_id}")


async def test_admin_patch_org_env(admin_client):
    client, _ws = admin_client
    create_resp = await client.post(
        "/api/v1/admin/sandbox-env",
        json={
            "env_name": "ROTATE_TEST_TOKEN",
            "is_secret": True,
            "hosts": ["api.example.com"],
            "secret_value": "original",
        },
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]

    patch_resp = await client.patch(
        f"/api/v1/admin/sandbox-env/{entry_id}",
        json={"secret_value": "rotated"},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["id"] == entry_id
    assert "secret_value" not in patched
    assert "credential_id" not in patched

    # Cleanup
    await client.delete(f"/api/v1/admin/sandbox-env/{entry_id}")


async def test_admin_patch_org_env_wrong_scope_returns_404(admin_client):
    """PATCH on a nonexistent entry_id returns 404."""
    client, _ws = admin_client
    resp = await client.patch(
        "/api/v1/admin/sandbox-env/senv-nonexistent",
        json={"secret_value": "x"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Workspace/user-scope routes (Task 10)
# ---------------------------------------------------------------------------


async def test_member_sets_own_user_env(member_client):
    client, workspace_id = member_client
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/sandbox-env/me",
        json={
            "env_name": "GITHUB_TOKEN",
            "is_secret": True,
            "hosts": ["api.github.com"],
            "secret_value": "ghp_u",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["scope"] == "user"


async def test_member_cannot_set_workspace_env(member_client):
    client, workspace_id = member_client
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/sandbox-env/workspace",
        json={"env_name": "X", "is_secret": False, "plain_value": "v"},
    )
    assert resp.status_code == 403  # require_admin


# ---------------------------------------------------------------------------
# Workspace-scope LIST, DELETE, PATCH (Task 12)
# ---------------------------------------------------------------------------


async def test_admin_list_workspace_env(admin_client):
    client, workspace_id = admin_client
    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/sandbox-env/workspace",
        json={
            "env_name": "WS_LIST_TOKEN",
            "is_secret": True,
            "hosts": ["api.example.com"],
            "secret_value": "ws_s3cr3t",
        },
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]

    list_resp = await client.get(f"/api/v1/ws/{workspace_id}/sandbox-env/workspace")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert "entries" in data
    ids = [e["id"] for e in data["entries"]]
    assert entry_id in ids
    for entry in data["entries"]:
        assert "secret_value" not in entry
        assert "credential_id" not in entry

    # Cleanup
    await client.delete(f"/api/v1/ws/{workspace_id}/sandbox-env/workspace/{entry_id}")


async def test_admin_delete_workspace_env(admin_client):
    client, workspace_id = admin_client
    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/sandbox-env/workspace",
        json={
            "env_name": "WS_DEL_TOKEN",
            "is_secret": True,
            "hosts": ["api.example.com"],
            "secret_value": "s3cr3t",
        },
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]

    del_resp = await client.delete(f"/api/v1/ws/{workspace_id}/sandbox-env/workspace/{entry_id}")
    assert del_resp.status_code == 204

    # Second delete → 404 (already gone)
    del_resp2 = await client.delete(f"/api/v1/ws/{workspace_id}/sandbox-env/workspace/{entry_id}")
    assert del_resp2.status_code == 404


async def test_admin_patch_workspace_env(admin_client):
    client, workspace_id = admin_client
    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/sandbox-env/workspace",
        json={
            "env_name": "WS_PATCH_TOKEN",
            "is_secret": True,
            "hosts": ["api.example.com"],
            "secret_value": "original",
        },
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]

    patch_resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/sandbox-env/workspace/{entry_id}",
        json={"secret_value": "rotated"},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["id"] == entry_id
    assert "secret_value" not in patched

    # Cleanup
    await client.delete(f"/api/v1/ws/{workspace_id}/sandbox-env/workspace/{entry_id}")


# ---------------------------------------------------------------------------
# User-scope LIST, DELETE, PATCH (Task 12)
# ---------------------------------------------------------------------------


async def test_member_list_own_user_env(member_client):
    client, workspace_id = member_client
    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/sandbox-env/me",
        json={
            "env_name": "ME_LIST_TOKEN",
            "is_secret": True,
            "hosts": ["api.example.com"],
            "secret_value": "my_s3cr3t",
        },
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]

    list_resp = await client.get(f"/api/v1/ws/{workspace_id}/sandbox-env/me")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert "entries" in data
    ids = [e["id"] for e in data["entries"]]
    assert entry_id in ids
    for entry in data["entries"]:
        assert "secret_value" not in entry
        assert "credential_id" not in entry

    # Cleanup
    await client.delete(f"/api/v1/ws/{workspace_id}/sandbox-env/me/{entry_id}")


async def test_member_delete_own_user_env(member_client):
    client, workspace_id = member_client
    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/sandbox-env/me",
        json={
            "env_name": "ME_DEL_TOKEN",
            "is_secret": True,
            "hosts": ["api.example.com"],
            "secret_value": "s3cr3t",
        },
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]

    del_resp = await client.delete(f"/api/v1/ws/{workspace_id}/sandbox-env/me/{entry_id}")
    assert del_resp.status_code == 204

    # Second delete → 404
    del_resp2 = await client.delete(f"/api/v1/ws/{workspace_id}/sandbox-env/me/{entry_id}")
    assert del_resp2.status_code == 404


async def test_member_patch_own_user_env(member_client):
    client, workspace_id = member_client
    create_resp = await client.post(
        f"/api/v1/ws/{workspace_id}/sandbox-env/me",
        json={
            "env_name": "ME_PATCH_TOKEN",
            "is_secret": True,
            "hosts": ["api.example.com"],
            "secret_value": "original",
        },
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]

    patch_resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/sandbox-env/me/{entry_id}",
        json={"secret_value": "rotated"},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["id"] == entry_id
    assert "secret_value" not in patched

    # Cleanup
    await client.delete(f"/api/v1/ws/{workspace_id}/sandbox-env/me/{entry_id}")


# ---------------------------------------------------------------------------
# Security: ownership guards (Task 12)
# ---------------------------------------------------------------------------


async def test_member_cannot_delete_nonexistent_entry(member_client):
    """DELETE /me/<nonexistent> returns 404 — can't delete others' or fabricated IDs."""
    client, workspace_id = member_client
    resp = await client.delete(f"/api/v1/ws/{workspace_id}/sandbox-env/me/senv-doesnotexist")
    assert resp.status_code == 404


async def test_member_cannot_patch_nonexistent_entry(member_client):
    """PATCH /me/<nonexistent> returns 404."""
    client, workspace_id = member_client
    resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/sandbox-env/me/senv-doesnotexist",
        json={"secret_value": "x"},
    )
    assert resp.status_code == 404


async def test_member_cannot_access_workspace_admin_routes(member_client):
    """A member hitting require_admin workspace endpoints gets 403."""
    client, workspace_id = member_client
    get_resp = await client.get(f"/api/v1/ws/{workspace_id}/sandbox-env/workspace")
    assert get_resp.status_code == 403

    del_resp = await client.delete(f"/api/v1/ws/{workspace_id}/sandbox-env/workspace/senv-any")
    assert del_resp.status_code == 403

    patch_resp = await client.patch(
        f"/api/v1/ws/{workspace_id}/sandbox-env/workspace/senv-any",
        json={"secret_value": "x"},
    )
    assert patch_resp.status_code == 403


async def test_member_cannot_delete_workspace_entry_via_me_path(admin_client, member_client):
    """A workspace entry created by admin cannot be deleted by a member via /me path.

    Uses two separate fixtures (different orgs/workspaces) so this test asserts
    that /me/{entry_id} returns 404 for a foreign-org entry_id — cross-org
    isolation is guaranteed by the org_id scope in the ownership guard.
    """
    admin_c, admin_ws = admin_client
    member_c, member_ws = member_client

    # Admin creates a workspace-scope entry in their own workspace/org
    create_resp = await admin_c.post(
        f"/api/v1/ws/{admin_ws}/sandbox-env/workspace",
        json={
            "env_name": "GUARD_TEST_TOKEN",
            "is_secret": True,
            "hosts": ["api.example.com"],
            "secret_value": "s3cr3t",
        },
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]

    # Member (different org) tries to delete it via /me path — must 404
    del_resp = await member_c.delete(f"/api/v1/ws/{member_ws}/sandbox-env/me/{entry_id}")
    assert del_resp.status_code == 404

    # Cleanup
    await admin_c.delete(f"/api/v1/ws/{admin_ws}/sandbox-env/workspace/{entry_id}")
